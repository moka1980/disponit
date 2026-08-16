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
    # Identiteten LÅSES her — den kan ikke endres etterpå. To krav må derfor
    # holde ved opprettelsen, begge Codex P2, og begge fordi et utkast som
    # bryter dem er DØDFØDT: eier kan ikke rette raden, bare forlate utkastet.
    #
    # FORMEN først: en id som ikke er skjemagyldig kan aldri skrives inn i
    # dokumentet, og en skjemagyldig id ville spriket fra raden. Rekkefølgen er
    # ikke tilfeldig — `"ACME"` er feil FORM, ikke for stor, og skal få den
    # beskjeden. Kontrollen står FØR idempotensposten: en forespørsel som aldri
    # kunne blitt et utkast skal heller ikke brenne nøkkelen.
    if not _POLICY_ID.match(policy_id or ""):
        conn.rollback()
        raise Aktiveringsfeil("policy_id_ugyldig", f"policy_id={policy_id!r}")
    # Så PLASSEN: levner identiteten ikke rom til en versjon i registerets
    # primærnøkkel, er ingen versjon eier senere kan skrive i stand til å få
    # plass. Da er det opprettelsen som skal si nei, ikke en validering hun
    # aldri kan tilfredsstille.
    if _nokkelbytes(tenant, policy_id) > _MAKS_NOKKELBYTES - _VERSJONSRESERVE:
        conn.rollback()
        raise Aktiveringsfeil(
            "utkast_feilformet",
            f"policy_id levner ikke plass til en versjon i registernøkkelen"
            f" ({_nokkelbytes(tenant, policy_id)} byte)")
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
        "SELECT innhold, status, utkastversjon, policy_id FROM policyutkast"
        " WHERE tenant=%s AND utkast_id=%s FOR UPDATE",
        (tenant, utkast_id)).fetchone()
    if rad is None:
        conn.rollback()
        raise Aktiveringsfeil("utkast_ukjent")
    innhold, status, ver, policy_id = rad
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
    # modus/vilkår osv.) — samme port motoren bruker (PR-014 R2).
    feil = _schema.valider_policy(innhold)
    # Identiteten og statusen er ingen skjemasak: skjemaet ser bare dokumentet,
    # og et dokument kan være helt gyldig og LIKEVEL oppgi en annen
    # `meta.policy_id` enn raden det ligger under, eller en `meta.status` som
    # ikke er den aktiveringen skriver (Codex P1). Valideringen er stedet å
    # stoppe begge: det er her innholdet FRYSES, og et frosset dokument kan ikke
    # rettes etterpå — bare erstattes av et nytt utkast. Fanget vi det først ved
    # rundeåpning, sto eier igjen med et validert utkast hun verken kunne
    # aktivere eller redigere. Avvikene legges i feillisten sammen med
    # skjemafeilene, så eier ser NØYAKTIG hva som må rettes i editoren.
    feil = list(feil) + _dokumentavvik(policy_id, innhold, tenant)
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


def hent_utkast_detalj(conn: psycopg.Connection, *, tenant: str, aktor: str,
                       request_id: str, utkast_id: str) -> dict:
    """Utkastet + diffen mot aktiv base + klassifisering + evt. åpen runde med
    attestasjoner. Rent lesende (ruller tilbake til slutt)."""
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
    (`schema.valider_policy` — skjema + semantikk, inkl. referanse-integritet og
    modus/vilkår, PR-014 R2) serveres ALDRI (fail-closed): den skal ikke kunne
    bli et «gyldig utgangspunkt»."""
    import yaml
    ut = []
    for f in sorted(_MAL_DIR.glob("bransjemal-*.yaml")):
        try:
            innhold = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(innhold, dict):
            continue
        if _schema.valider_policy(innhold):
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


#: Statusen den STYRTE aktiveringen skriver i registeret (`aktiver_policy`
#: steg 5: `status = 'produksjon'`). Hvilke statuser en LASTET policy får ha er
#: miljøstyrt (`policyregister.tillatte_statuser`) — hva fire-øyne-veien
#: aktiverer, er det ikke: den aktiverer produksjonspolicyer, i alle miljøer.
_AKTIVERINGSSTATUS = "produksjon"


def _meta(innhold) -> dict:
    """`innhold.meta` som dict — tomt kart om den mangler eller er noe annet."""
    m = innhold.get("meta") if isinstance(innhold, dict) else None
    return m if isinstance(m, dict) else {}


#: Identitetsformen — en KOPI av skjemaets `meta.policy_id`
#: (`policy-schema-v0.2.json`: `^[a-z0-9-]+$`, `minLength: 3`).
#: `test_policy_id_monsteret_speiler_skjemaet` binder de to sammen, så de ikke
#: kan gli fra hverandre — samme grep som `engine._POLICY_ID_MONSTER`.
#:
#: Kravet må stå på RADEN, ikke bare i dokumentet (Codex P2). Endepunktet tok
#: `policy_id` fra toppnivået i forespørselen og krevde bare en ikke-tom
#: streng, så `"ACME"` og `" acme "` ble lagret som radens identitet — og den
#: kan aldri endres (`rediger_utkast` rører den ikke, og det er med vilje).
#: Etter at identitetskravet kom, var et slikt utkast dødfødt uansett hvilken
#: vei eier prøvde: skriver hun radens id inn i dokumentet, bryter den
#: skjemaet; velger hun en skjemagyldig id, spriker den fra raden. Den ENESTE
#: utveien var å forlate utkastet — nøyaktig fella `2d94532` lukket for
#: dokumentets side.
#:
#: Derfor avvises den, ikke normaliseres: `" acme "` → `"acme"` ville vært en
#: gjetning på hvilken policy eier mente, og identiteten er det ene feltet som
#: ikke kan rettes etterpå. Editoren trimmer allerede FØR den sender (`2d94532`),
#: så dette rammer bare direkte API-kall — der et tydelig avslag er riktig svar.
_POLICY_ID = re.compile(r"^[a-z0-9-]{3,}$")


def _dokumentidentitet_avvik(policy_id: str, innhold) -> str | None:
    """-> forklaringen når dokumentets `meta.policy_id` IKKE er den utkastet
    er registrert under, ellers None.

    `policyutkast.policy_id` og `innhold.meta.policy_id` er to felter, men ÉN
    sak. Endepunktet tar dem fra hver sin del av forespørselen, og
    redigeringsveien skriver nytt innhold uten å røre radens `policy_id` — så
    de kan skille lag uten at noe formatkrav protesterer.

    Aktiveringen lagrer da innholdet under registerets id, mens motoren bygger
    beslutningens policyreferanse fra DOKUMENTET
    (`engine.policyreferanse`: `<meta.policy_id>@<meta.versjon>/<handling>`).
    Revisjonsposten og M-37-gjenopprettingen slår derfor opp en id det ikke
    finnes noen aktiv rad for, og sakene faller ut av automatisk behandling —
    uten at noe utsagn underveis så galt ut.
    """
    innbakt = _meta(innhold).get("policy_id")
    if innbakt == policy_id:
        return None
    return (f"meta.policy_id {innbakt!r} er ikke utkastets policy_id"
            f" {policy_id!r}")


def _dokumentavvik(policy_id: str, innhold, tenant: str = "") -> list[str]:
    """Alt som gjør at det frosne dokumentet ikke KAN aktiveres slik det står.

    Kravene er identiske med portens (`_krev_dokumentidentitet`,
    `_krev_produksjonsstatus`) og databasens (migrasjon 022/023) — samlet her
    fordi valideringen trenger dem som TEKST, ikke som feil: der er de ennå til
    å rette. Etter frysingen er de ikke det.
    """
    avvik = []
    identitet = _dokumentidentitet_avvik(policy_id, innhold)
    if identitet is not None:
        avvik.append(identitet)
    status = _meta(innhold).get("status")
    if status != _AKTIVERINGSSTATUS:
        avvik.append(
            f"meta.status {status!r} må være {_AKTIVERINGSSTATUS!r} — en"
            " fire-øyne-runde aktiverer policyen som produksjonspolicy")
    # Versjonen måles her OG i `_krev_ny_versjon`, men med ulik hensikt: der er
    # den en feil, her er den en tekst eier kan handle på. Skjemaet slipper
    # gjennom to former registeret ikke kan bære — unicode-sifre (Pythons `\d`,
    # og dermed `jsonschema`, godtar hele desimalsiffer-kategorien mens
    # databasen krever `[0-9]`) og en nøkkel som sprenger primærnøkkelens
    # btree-oppføring. Begge må stanses FØR frysingen: etterpå kan versjonen
    # ikke økes, og runden er tapt.
    versjon = _meta(innhold).get("versjon")
    if isinstance(versjon, str) and not _SEMVER.match(versjon):
        avvik.append(
            f"meta.versjon {versjon[:40]!r} må være tre tall skilt med punktum"
            " (ASCII-sifre, formen 1.2.3)")
    elif isinstance(versjon, str):
        stor = _nokkelbytes(tenant, policy_id, versjon)
        if stor > _MAKS_NOKKELBYTES:
            avvik.append(
                f"policy_id + meta.versjon er {stor} byte til sammen — maks er"
                f" {_MAKS_NOKKELBYTES} (de deler registerets primærnøkkel)")
    return avvik


def _krev_dokumentidentitet(policy_id: str, innhold) -> None:
    """Som `_dokumentidentitet_avvik`, men for de styrte veiene: kaster
    `Aktiveringsfeil("policy_id_avvik")`.

    Kontrollen ligger både i `valider_utkast` (der dokumentet fryses) og her,
    fordi et utkast validert FØR denne kontrollen fantes kan bære avviket inn i
    en runde. Ingen skal signere på et dokument som aktiveres under en annen
    identitet enn den det selv oppgir."""
    avvik = _dokumentidentitet_avvik(policy_id, innhold)
    if avvik is not None:
        raise Aktiveringsfeil("policy_id_avvik", avvik)


def _krev_produksjonsstatus(innhold) -> None:
    """Dokumentets `meta.status` MÅ være den registerstatusen aktiveringen
    skriver. Kaster `Aktiveringsfeil("status_ikke_produksjon")`.

    Skjemaet tillater tre verdier (`utkast`, `validert_pilot`, `produksjon`),
    så en policy merket `utkast` er fullt skjemagyldig — og gikk hele veien
    gjennom fire-øyne. Raden ble da skrevet som `produksjon` over et dokument
    som sier noe annet, og `hent_aktiv` avviser NØYAKTIG den kombinasjonen:
    kolonnen brukes til filtrering mens `meta.status` havner i loggposten, så
    spriker de, er det uklart hva beslutningen ble tatt under. Aktiveringen
    svarte «aktivert», og hver påfølgende beslutning svarte `PolicyKorrupt`.

    Å skrive om statusen ved aktivering er stengt (frosset innhold, bundet
    `innholds_hash`), så kravet må komme FØR noen signerer — mens utkastet
    ennå kan rettes."""
    status = _meta(innhold).get("status")
    if status != _AKTIVERINGSSTATUS:
        raise Aktiveringsfeil("status_ikke_produksjon",
                              f"meta.status={status!r}")


#: `aktiver_policy` reiser dokumentinvariantene sine som `check_violation` og
#: NAVNGIR bruddet (`USING CONSTRAINT`). Navnet er det eneste som skiller dem
#: fra hverandre i feilen kalleren ser — uten det ville et identitetsavvik blitt
#: rapportert som `versjon_i_bruk`: riktig kansellering, feil beskjed til eier.
#: Ukjent/uten navn → versjonsinvariantene fra 020, som er de eldste.
_DOKUMENTBRUDD = {"dokument_policy_id": "dokument_avvik",
                  "dokument_status": "dokument_avvik"}

#: Skjemaets versjonsform (`policy-schema-v0.2.json`: `meta.versjon`), men med
#: ASCII-sifre EKSPLISITT (Codex P2). Pythons `\d` matcher hele Unicodes
#: desimalsiffer-kategori, og det gjør `jsonschema` også — så «١.٠.٠» er
#: skjemagyldig. Databasen bruker `[0-9]` (migrasjon 020–024) og avviser den, og
#: nøkkelen under sammenligner sifrene som TEKST, der «١» sorterer over «2».
#: Uten dette godtok porten altså en versjon som er både feilordnet og
#: ulagringsbar, åpnet runden, og lot bruddet komme etter attestasjonene — med
#: en kansellert runde som resultat. De to gatene skal måle det samme.
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
#: Største SAMLEDE nøkkel registeret kan lagre, i byte. `policyer_pkey` er
#: `(tenant, policy_id, versjon)`, og en btree-oppføring har et hardt tak
#: (~2704 byte på 8 KiB-sider) som de tre feltene DELER. Derfor er ingen av dem
#: trygg målt for seg (Codex P2): `policy_id` har ingen maks i skjemaet, så en
#: id på et par kilobyte kan spise hele budsjettet alene, og da hjelper det ikke
#: at versjonen er kort. Skjemaet setter heller ingen grense på versjonen, og
#: API-ets kroppsgrense slipper gjennom ledd på titusener av sifre.
#:
#: Uten kontrollen passerte en slik nøkkel ALLE kontrollene og veltet først på
#: INSERT-en i aktiveringen, som `ProgramLimitExceeded`: en uhåndtert 500 midt i
#: fire-øyne-runden, etter at godkjennerne hadde signert.
#:
#: 2400 lar det stå ~300 byte igjen til indeksoppføringens eget overhead
#: (tuppelhode, null-bitmap, varlena-hoder og justering per felt) — rikelig, og
#: fortsatt hundrevis av ganger mer enn en ekte id + semver trenger. Migrasjon
#: 024 håndhever samme tall med `octet_length`, som er nøyaktig samme mål.
_MAKS_NOKKELBYTES = 2400

#: Plass som MÅ stå igjen til versjonen når identiteten låses (ved opprettelse).
#: `policy_id` kan ikke endres etterpå, så en id som ikke levner rom til en
#: versjon gir et utkast som aldri kan aktiveres — uansett hva eier gjør
#: etterpå. Da er det opprettelsen som skal si nei, ikke valideringen.
_VERSJONSRESERVE = 64


def _nokkelbytes(*deler: str) -> int:
    """Nøkkelens størrelse slik Postgres måler den (`octet_length`, UTF-8)."""
    return sum(len(d.encode("utf-8")) for d in deler if isinstance(d, str))
#: Tallpunktet versjon — semver, men også de eldre «1»/«2»-radene den styrte
#: aktiveringen skrev før migrasjon 020. Alt annet sammenlignes ikke.
_TALLVERSJON = re.compile(r"^[0-9]+(\.[0-9]+)*$")


def _versjonsnokkel(versjon: str, ledd: int) -> tuple[tuple[int, str], ...]:
    """Tallpunktet versjon som sammenlignbar nøkkel, NULLPADDET til `ledd`.

    Paddingen er ikke kosmetikk. Uten den sorterer tuppelsammenligningen
    «2.0.0» OVER «2» — prefikset er likt, og den lengste vinner. Det er
    nøyaktig formen de eldre radene bærer («1», «2», skrevet av telleren
    migrasjon 020 fjerner), så en aktiv «2»-rad ville sluppet gjennom
    dokumentversjonen «2.0.0»: samme versjon, ikke en nyere. Padder vi begge
    til samme bredde, blir «2» → 2.0.0 og de to er like — som de er.

    Leddet sammenlignes som (ANTALL SIFRE, sifrene) etter at innledende nuller
    er strøket, ikke som `int` (Codex P2). For ikke-negative heltall gir det
    nøyaktig tallordenen — og det er UBEGRENSET. `int()` er ikke det: CPython
    nekter å konvertere strenger over 4300 sifre (`sys.set_int_max_str_digits`),
    og skjemaet setter ingen grense på hvor mange sifre `meta.versjon` kan ha.
    Et skjemagyldig utkast kunne altså felt PORTEN med `ValueError` — samme
    sykdom som `::int[]`-casten i databasen hadde, bare på denne siden.
    """
    ledd_ut = [d.lstrip("0") or "0" for d in versjon.split(".")]
    ledd_ut += ["0"] * (ledd - len(ledd_ut))
    return tuple((len(d), d) for d in ledd_ut)


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
    ny = _meta(ny_innhold).get("versjon")
    if not isinstance(ny, str) or not _SEMVER.match(ny) \
            or _nokkelbytes(tenant, policy_id, ny) > _MAKS_NOKKELBYTES:
        # Formen OG plassen: en nøkkel registeret ikke kan lagre er like umulig
        # å aktivere som en versjon som mangler (se `_MAKS_NOKKELBYTES`), og
        # skal stoppes her — ikke som en indeksfeil etter to signaturer.
        raise Aktiveringsfeil("versjon_mangler", f"meta.versjon={ny!r:.80}")
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

    aktiv_versjon = _hode_aktiv_versjon(conn, tenant, policy_id)
    # Ingen runde åpnes på en base vi ikke stoler på (Codex P1): en usynk peker
    # ville gitt godkjennerne en diff mot feil base, og runden kunne uansett
    # ikke aktiveres etter en reparasjon.
    _krev_peker_synk(conn, tenant, policy_id, aktiv_versjon)
    # Og ingen runde åpnes på et utkast som ikke KAN lagres: identiteten det
    # bærer må være radens egen (migrasjon 022), statusen må være den
    # aktiveringen skriver (migrasjon 023), og versjonen må være semantisk,
    # ubrukt og nyere enn den aktive (migrasjon 020).
    _krev_dokumentidentitet(policy_id, ny_innhold)
    _krev_produksjonsstatus(ny_innhold)
    _krev_ny_versjon(conn, tenant, policy_id, ny_innhold, aktiv_versjon)
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
        # Samme argument for identiteten, statusen og versjonen: en signatur på
        # et utkast som ikke kan lagres — eller som ville blitt lagret under en
        # ANNEN policy enn den det selv oppgir, eller som en policy det ikke
        # sier at det er — er like verdiløs som en signatur på feil base.
        _krev_dokumentidentitet(policy_id, ny_innhold)
        _krev_produksjonsstatus(ny_innhold)
        _krev_ny_versjon(conn, tenant, policy_id, ny_innhold, aktiv_versjon)
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
    except psycopg.errors.UniqueViolation as e:
        # INSERT-en i steg 5 kan bryte TO ulike unike krav, og de betyr helt
        # ulike ting for eier (Codex P2):
        #
        #   * `en_aktiv_per_policy` — det finnes en aktiv policyrad pekeren
        #     ikke kjenner til. Pekeren er ute av synk og MÅ repareres; et nytt
        #     forsøk hjelper ikke, men runden er fortsatt gyldig etterpå. Den
        #     får derfor stå, og attestasjonen består.
        #   * `policyer_pkey` — versjonen utkastet bærer ble registrert av en
        #     annen skriver i vinduet mellom funksjonens egen ubrukt-kontroll og
        #     INSERT-en. Pekeren er helt i synk; det er VERSJONEN som er borte,
        #     permanent: innholdet er frosset, så den kan ikke økes uten et nytt
        #     utkast. Meldte vi «reparer dataene» her, ville eier lett etter en
        #     usynk som ikke finnes — og runden ville blitt stående åpen og se
        #     levende ut selv om den beviselig aldri kan aktiveres.
        #
        # Ukjent/uten navn behandles som pekerdrift, som før: INSERT-en er den
        # eneste setningen i funksjonen som kan reise et unikt brudd, og
        # alternativet ville vært å kansellere en runde på et brudd vi ikke har
        # forstått. Å bevare er det reversible valget.
        conn.execute("ROLLBACK TO SAVEPOINT aktiveringsforsok")
        if e.diag.constraint_name == "policyer_pkey":
            return _kanseller_runde(conn, tenant, idempotency_key, utkast_id,
                                    policy_id, r_nr, "versjon_i_bruk")
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "aktiv_peker_usynk", "utkast_id": utkast_id,
            "policy_id": policy_id, "runde": r_nr})
    except psycopg.errors.CheckViolation as e:
        # Dokumentinvariantene i `aktiver_policy`: versjonen (migrasjon 020) er
        # borte, alt registrert eller ikke nyere enn den aktive — eller
        # identiteten (migrasjon 022) er en annen enn radens. Kontrollene i
        # steg 5b fanger det som var der da runden ble bygget; hit kommer bare
        # et avvik som oppsto UTENOM den styrte veien i vinduet etterpå. Da er
        # runden død: innholdet er frosset, så verken versjon eller identitet
        # kan rettes uten et nytt utkast og nye signaturer. Runden kanselleres
        # derfor med det samme — en runde som beviselig aldri kan aktiveres
        # skal ikke stå åpen og se levende ut. Signaturene består (append-only);
        # det er sporet av hva som faktisk ble godkjent.
        #
        # Funksjonen NAVNGIR bruddet (`USING CONSTRAINT`), for de to slagene
        # deler feilkode og krever hver sin forklaring til eier: «gi utkastet
        # en ny versjon» hjelper ikke den som har skrevet feil policy_id.
        conn.execute("ROLLBACK TO SAVEPOINT aktiveringsforsok")
        return _kanseller_runde(
            conn, tenant, idempotency_key, utkast_id, policy_id, r_nr,
            _DOKUMENTBRUDD.get(e.diag.constraint_name, "versjon_i_bruk"))
    conn.execute("RELEASE SAVEPOINT aktiveringsforsok")

    return _fullfor(conn, tenant, idempotency_key, {
        "utfall": "aktivert", "utkast_id": utkast_id, "policy_id": policy_id,
        "versjon": ny_versjon, "runde": r_nr, "risikoklasse": r_risiko})


def _kanseller_runde(conn, tenant: str, idempotency_key: str, utkast_id: str,
                     policy_id: str, runde: int, utfall: str) -> dict:
    """Lukk en runde som beviselig ALDRI kan aktiveres, og svar deterministisk.

    Felles for utfallene som havner her: det frosne utkastet kan ikke lagres
    slik det står (versjonen er tatt, identiteten eller statusen stemmer ikke),
    og innholdet kan ikke rettes — bare erstattes. En runde som ser levende ut
    og aldri kan lykkes, er verre enn ingen runde: godkjennere venter på noe som
    ikke kommer. Signaturene består (append-only); de er sporet av hva som
    faktisk ble godkjent."""
    conn.execute("UPDATE aktiveringsrunde SET status='kansellert'"
                 " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
                 (tenant, utkast_id, runde))
    return _fullfor(conn, tenant, idempotency_key, {
        "utfall": utfall, "utkast_id": utkast_id, "policy_id": policy_id,
        "runde": runde})


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
