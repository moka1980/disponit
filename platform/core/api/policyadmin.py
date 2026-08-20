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

from db.pg import laas_policy_delt, sett_kontekst
from policy_validator import klassifikator, policydiff, semantikk
from policy_validator import schema as _schema

from . import policyregister as _pr
from . import varsel
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
                   rollback_av_versjon: str | None = None,
                   rollback_av_generasjon: int | None = None) -> dict:
    """Opprett et nytt utkast (status `utkast`). Fanger gjeldende aktive versjon
    + hash som `basert_pa_*` for konfliktdeteksjon (§4). Idempotent (P1 R3):
    en replay returnerer NØYAKTIG samme utkast_id. Kalleren eier tx.

    En RULLBAKK må levere kilderadens `generasjon`, hentet SAMMEN med
    innholdet den kopierer: opphavet lagres som generasjonens identitet,
    ikke som versjonsnummeret eller innholdet — begge kan gjenskapes etter
    en sletting, generasjonstallet kan ikke. Se migrasjon 047."""
    sett_kontekst(conn, tenant, aktor, request_id)
    if not isinstance(innhold, dict):
        conn.rollback()
        raise Aktiveringsfeil("utkast_feilformet")
    # Idempotensen FØRST — den er eldre enn ethvert krav vi legger på
    # innholdet. En nøkkel som lyktes før en innstramming ble rullet ut har et
    # lagret svar, og replay-kontrakten er NØYAKTIG det svaret: en klient som
    # mistet responsen og prøver på nytt skal få utkastet sitt, ikke en
    # avvisning av en id som alt ER raden hennes. Sto kontrollen foran, ville
    # innstrammingen gjort gamle, vellykkede opprettelser usynlige for eieren
    # deres — utkastet finnes, men hun får aldri vite id-en. (Codex P2.)
    tilstand, lagret = _idempotent_start(conn, tenant, idempotency_key,
                                         input_hash, request_id)
    if tilstand == "replay":
        conn.rollback()
        return lagret
    if tilstand == "konflikt":
        conn.rollback()
        raise Aktiveringsfeil("idempotenskonflikt")
    # Identiteten LÅSES her — den kan ikke endres etterpå. To krav må derfor
    # holde ved opprettelsen, begge Codex P2, og begge fordi et utkast som
    # bryter dem er DØDFØDT: eier kan ikke rette raden, bare forlate utkastet.
    # Kravene gjelder bare et FERSKT krav på nøkkelen (`ny`), og `rollback()`
    # under ruller posten tilbake sammen med resten av transaksjonen: en
    # forespørsel som aldri kunne blitt et utkast brenner ikke nøkkelen.
    #
    # Selve prøven bor i `nytt_utkast_avvik` — flatene spør den FØR de tilbyr
    # en handling som ellers alltid ender i 400 (Codex P2).
    avvik = nytt_utkast_avvik(tenant, policy_id)
    if avvik:
        conn.rollback()
        raise Aktiveringsfeil(*avvik)
    # Identiteten er godkjent. Så INNHOLDET, og her retter vi i stedet for å
    # avvise — statusen er ikke eiers valg (se under).
    #
    # Dokumentets `meta.status` settes HER, ikke av mennesket.
    #
    # Bransjemalene bærer `status: utkast` — riktig for en mal. Men aktiveringen
    # skriver `produksjon` i registeret, og `hent_aktiv` krever at de to er
    # like; et utkast som bar malens status videre ble derfor aktivert til en
    # policy som ble avvist som korrupt ved HVER beslutning. Eier laget to
    # slike (`tjenestebedrift1`, `tjenestebedrift2`) uten at noe sa fra.
    #
    # Editoren eksponerer ikke feltet, og skal ikke gjøre det: arbeidsflyt-
    # statusen ligger i `policyutkast.status` (utkast → validert → godkjent →
    # aktivert). `meta.status` beskriver hva dokumentet ER som registrert
    # policy, altså en KONSEKVENS av å bli aktivert — ikke et valg. Å be
    # mennesket skrive «produksjon» i et fritekstfelt ville bare flyttet
    # feilen.
    #
    # Normaliseringen skjer ved opprettelsen, FØR valideringen fryser
    # `innholds_hash`: det som valideres, differes og attesteres er da det
    # samme dokumentet som blir aktivert.
    if isinstance(innhold.get("meta"), dict):
        innhold = {**innhold,
                   "meta": {**innhold["meta"], "status": "produksjon"}}
    _, base_hash, aktiv = _base_med_versjon(conn, tenant, policy_id)
    # `meta.versjon` normaliseres etter samme prinsipp som `meta.status` over:
    # et utkast som er DØDFØDT slik det opprettes, skal ikke opprettes slik.
    # Redigerer man en eksisterende policy, arver utkastet dokumentets egen
    # versjon — altså nøyaktig den som er aktiv — og eiers utkast 17/8 bar
    # 0.3.0 hele veien gjennom validering før rundeåpningen avviste det med
    # et uforklart 409. En versjon som alt er opptatt byttes derfor med den
    # neste ledige VED OPPRETTELSEN, mens en versjon eier selv har satt
    # HØYERE ikke røres (den er et gyldig valg, ikke en arv). Valideringen
    # (`_versjonsavvik`) står uansett som port for det som redigeres inn
    # senere.
    if isinstance(innhold.get("meta"), dict) \
            and isinstance(innhold["meta"].get("versjon"), str) \
            and _SEMVER.fullmatch(innhold["meta"]["versjon"]):
        try:
            _krev_ny_versjon(conn, tenant, policy_id, innhold, aktiv)
        except Aktiveringsfeil:
            forslag = _neste_ledige_versjon(conn, tenant, policy_id, aktiv)
            if forslag:
                innhold = {**innhold,
                           "meta": {**innhold["meta"], "versjon": forslag}}
    utkast_id = "u-" + secrets.token_hex(8)
    # Opphavet bindes til GENERASJONEN, ikke til nummeret og ikke til
    # innholdet (Codex P2). Begge de to kan gjenskapes: nummeret frigjøres
    # av sletting, og det samme dokumentet kan settes inn igjen — da er
    # hashen lik og generasjonen en annen. Tallet leses av kalleren SAMMEN
    # med innholdet kopien er tatt fra, ikke i et nytt oppslag her: en
    # gjenskaping mellom de to ville ellers bundet kopien til en generasjon
    # den aldri kom fra. En rullbakk uten kildegenerasjon får NULL —
    # historikken sier da «kilden er ikke bundet» i stedet for å påstå noe.
    rb_gen = (rollback_av_generasjon if rollback_av_versjon is not None
              and isinstance(rollback_av_generasjon, int) else None)
    conn.execute(
        "INSERT INTO policyutkast (tenant, utkast_id, policy_id,"
        " basert_pa_versjon, basert_pa_hash, rollback_av_versjon,"
        " rollback_av_generasjon, innhold, opprettet_av)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
        (tenant, utkast_id, policy_id, aktiv, base_hash, rollback_av_versjon,
         rb_gen, json.dumps(innhold), aktor))
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


def _krev_malautorisasjonsvilkar(conn: psycopg.Connection,
                                 innhold) -> list[str]:
    """Feilliste: `ekstern_lesing`-handlinger uten plattformvilkår (047).

    HVILKE handlinger porten gjelder for avgjøres av `_er_ekstern_lesing` —
    den SAMME klassifiseringen aktiveringsporten bruker (Codex P2): koden
    først, registeret bare der koden ikke selv stiller kravet. Et rent
    registeroppslag her var ikke bare en annen mening om samme handling,
    det var en STILLERE mening. For en kodefestet type som
    `kontroll.wcag.nettsted` med manglende eller feil registerrad
    (`sideeffektfri`) krevde `_krev_ekstern_lesing_port` fortsatt et
    målautorisasjonsvilkår, mens valideringen her fant ingen ekstern
    handling i det hele tatt og FRØS utkastet som gyldig. Eier møtte da
    kravet først ved rundeåpning eller attestering — etter frysingen, der
    innholdet ikke lenger kan rettes, bare erstattes. Hele poenget med å
    måle kravet i valideringen er at det skal komme mens utkastet ennå er
    redigerbart, og da må de to veiene klassifisere likt.

    Vilkårskravet leses fra `malautorisasjonsvilkar` — ingen hardkodet
    liste (port 32) — og måles mot typens `malautorisasjonsdomene`, samme
    domenebinding som aktiveringsporten krever. En handling som er
    ekstern lesing UTEN en målautorisasjonsbærende oppdragstype kan ikke
    aktiveres i det hele tatt (porten avviser den med
    `malautorisasjon_mangler`); den får derfor sin egen linje her i
    stedet for å bli stående som tilsynelatende gyldig.

    En handling hvis id verken er en kodefestet type eller en registrert
    oppdragstype er ikke denne portens sak: den er enten en intern
    handling (motoren) eller fanges av bestillingsveien."""
    if not isinstance(innhold, dict):
        return []
    handlinger = innhold.get("handlinger")
    if not isinstance(handlinger, list):
        return []
    feil = []
    for h in handlinger:
        if not isinstance(h, dict):
            continue
        ekstern, t = _er_ekstern_lesing(conn, h)
        if not ekstern:
            continue
        hid = h.get("id") if isinstance(h.get("id"), str) else "?"
        if t is None or not t.krever_malautorisasjon \
                or t.malautorisasjonsdomene is None:
            feil.append(
                f"handling '{hid}': ekstern_lesing uten"
                " målautorisasjonsbærende oppdragstype — det finnes ikke"
                " noe målautorisasjonsvilkår som kan telle, og handlingen"
                " kan ikke aktiveres")
            continue
        # Ustrukturert `vilkaar` er en skjemasak og er alt rapportert av
        # `valider_ny_policy`; her leses bare det som ER lesbart, slik at
        # feillisten ikke drukner i en TypeError fra et halvferdig utkast.
        vilkaar = h.get("vilkaar") if isinstance(h.get("vilkaar"), list) \
            else []
        navn = [v.get("navn") for v in vilkaar
                if isinstance(v, dict) and isinstance(v.get("navn"), str)]
        navn += [v for v in vilkaar if isinstance(v, str)]
        if not navn or conn.execute(
                "SELECT 1 FROM malautorisasjonsvilkar WHERE"
                " vilkar_type = ANY(%s) AND maldomene = %s LIMIT 1",
                (navn, t.malautorisasjonsdomene)).fetchone() is None:
            lovlige = [r[0] for r in conn.execute(
                "SELECT vilkar_type FROM malautorisasjonsvilkar"
                " WHERE maldomene = %s ORDER BY vilkar_type",
                (t.malautorisasjonsdomene,)).fetchall()]
            feil.append(
                f"handling '{hid}': ekstern_lesing krever et "
                f"målautorisasjonsvilkår for {t.malautorisasjonsdomene} "
                f"({', '.join(lovlige) or 'ingen registrert ennå'})")
    return feil


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
    # modus/vilkår osv.) — samme port motoren bruker (PR-014 R2). Her i
    # INNFØRINGS-varianten: utkastet skal aktiveres, og porten inn er stedet
    # der framoverrettede krav (entydig verifikator-id, strenge mønsterankre)
    # hører hjemme — ikke i revalideringen av det som alt er aktivt (Codex P1
    # på #63).
    feil = _schema.valider_ny_policy(innhold)
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
    # 047 (klarsignal §5/port 34): en handling hvis oppdragstype er
    # `ekstern_lesing` MÅ bære et plattform-målautorisasjonsvilkår —
    # målt her, FØR runden, ikke først i modulaktiveringsporten (036).
    # Dette er samtidig fjerningsvernet (port 31): å redigere bort
    # vilkåret gjør utkastet ugyldig, uansett hvilken flate som prøvde.
    #
    # REGISTERAVVIK, ikke dokumentfeil (Codex P2). Dommen ser på utkastet,
    # men den FELLES av registeret: `_er_ekstern_lesing` leser
    # `modulkontrakt` og `oppdragstype_register`, og vilkårslista leses fra
    # den append-only `malautorisasjonsvilkar`. Alle tre flytter seg — det
    # er nettopp derfor kravet ikke er hardkodet (port 32). Kjørte
    # valideringen før drift hadde registrert vilkåret eller kontrakten, og
    # eier prøvde igjen med flatens stabile `valNokkel` etter at det var på
    # plass, ville `_idempotent_start` replayet den gamle dommen uten å
    # spørre det reparerte registeret — og det uendrede utkastet var umulig
    # å validere fra den visningen til eier tilfeldigvis tvang fram en ny
    # render. Samme resonnement som `_versjonsavvik` under, samme bøtte.
    reg_feil = _krev_malautorisasjonsvilkar(conn, innhold)
    # Versjonen måles mot REGISTERET her — ikke først ved rundeåpning. Eiers
    # utkast 17/8 bar den aktive policyens egen versjon (0.3.0), gikk
    # gjennom valideringen med glans, og døde så med `versjon_i_bruk` i et
    # 409 ingen flate forklarte. Valideringen er stedet eier fortsatt kan
    # RETTE: etter frysingen kan versjonen ikke økes, og et validert utkast
    # med opptatt versjon er en blindgate (den kan bare gjenåpnes/erstattes).
    # Porten `_krev_ny_versjon` består uendret ved rundeåpning og attestering;
    # her brukes den som PRØVE, så de to aldri kan mene noe ulikt om samme
    # versjon.
    reg_feil += _versjonsavvik(conn, tenant, policy_id, innhold)
    if feil:
        # Ugyldig CACHES også (bundet til versjonen): en retry med samme nøkkel
        # får samme svar; et endret utkast (ny versjon) → egen nøkkel/konflikt.
        # Registeravviket tas med som tekst — utkastet er uansett ugyldig av
        # dokumentgrunner, og de kan ikke rettes uten en ny versjon (ny nøkkel).
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "ugyldig", "utkast_id": utkast_id, "feil": feil + reg_feil})
    if reg_feil:
        # Er REGISTERET eneste innvending, caches svaret IKKE (Codex P2):
        # dokumentfeilene over er egenskaper ved det uforanderlige utkastet og
        # tåler et replay, men registeret flytter seg — slettes policyen som
        # holder versjonen (slettingen frigjør uttrykkelig versjonsnumrene),
        # eller registreres vilkåret/kontrakten en `ekstern_lesing`-handling
        # manglet, er det samme utkastet gyldig. Et cachet «ugyldig» ville da
        # replayet en foreldet dom uten å spørre registeret, og flaten
        # gjenbruker med rette nøkkelen sin per render. Rollback tar claimet
        # med seg — en validering som ikke frøs noe skal ikke brenne nøkkelen.
        conn.rollback()
        return {"utfall": "ugyldig", "utkast_id": utkast_id, "feil": reg_feil}
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


def _gjenstaar_effektivt(pakrevd: int, antall: int, ikke_forfatter: int) -> int:
    """Hvor mange attestasjoner som FAKTISK gjenstår før runden kan aktiveres.

    Terskelen (V6) har to betingelser: `antall >= pakrevd` OG minst én
    attestasjon fra en ikke-forfatter. `pakrevd - antall` teller bare den
    første, og for en `INNSNEVRER`/`NØYTRAL`-runde er `pakrevd` 1 — så når
    forfatteren attesterer først, blir differansen 0 mens runden fortsatt
    venter på den uavhengige godkjenneren (Codex P2). Han fikk da beskjed om
    at null attestasjoner gjenstår, samtidig som hans egen var den eneste som
    manglet. E-posten sa det samme: den rendres fra de samme parametrene.

    Kravet om en uavhengig attestasjon er derfor et eget ledd i maksimum: står
    det igjen, gjenstår det minst én uansett hva differansen sier.

    Flaten regner det samme ut fra `mangler_uavhengig` i svaret
    (`gjenstaarIgjen`); det som skrives inn i varselet har ikke det feltet å
    støtte seg på, og må bære tallet ferdig.
    """
    return max(0, pakrevd - antall, 0 if ikke_forfatter >= 1 else 1)


def _forson_rundevarsling(conn: psycopg.Connection, tenant: str, aktor: str,
                          request_id: str, lagret: dict, naa) -> int:
    """Kjør varslingen for en alt åpnet runde på nytt. -> antall opprettet.

    Varslingen er best effort med vilje (regel 2 i `varsel`): feiler den, blir
    runden committet likevel, for en fullmaktsendring skal ikke kunne velte av
    en varslingsfeil. Men prisen var at feilen var ENDELIG (Codex P2). Runden
    sto åpen og ventet på godkjennere som aldri fikk vite det, og en klient som
    prøvde på nytt med samme idempotensnøkkel gikk ut i `replay`-grenen FØR
    varslingen i det hele tatt ble forsøkt — nettopp den retryen som skulle
    reparert det, hoppet over reparasjonen.

    Varslingen er idempotent i seg selv (`ON CONFLICT DO NOTHING` på
    hendelsesnøkkelen), så en gjentakelse er gratis når ingenting mangler og
    fyller nøyaktig hullet når noe gjør det. Det er derfor dette kan gjøres
    uten å telle eller huske noe: TILSTANDEN er allerede lagret — det er den
    åpne runden.

    Bare en runde som fortsatt VENTER varsles på nytt. Er den attestert,
    aktivert, kansellert eller forfalt, er det ikke lenger sant at den venter
    på noen, og et varsel opprettet her ville vært en ny løgn i stedet for en
    reparasjon av en gammel. `_runde_status` avgjør det — samme predikat som
    skrive- og lesestien ellers, så de tre aldri blir uenige om «forfalt».

    Oppslaget kjøres SKJERMET. Det er et spørsmål til den samme databasen som
    nettopp kan ha sviktet, og et uskjermet oppslag her ville veltet replayen —
    altså gjort varslingen til det den lovte å aldri bli: noe som velter
    handlingen.
    """
    utkast_id, runde = lagret.get("utkast_id"), lagret.get("runde")
    if not utkast_id or not runde:
        return 0

    def _les_under_laas():
        # RUNDEN LÅSES FØRST (Codex P2). Uten låsen var «åpen» bare sant i det
        # øyeblikket spørringen svarte: den siste attesteringen kunne lukke
        # runden og kjøre `pensjoner_runde` i vinduet før innsettingen under,
        # og da traff ikke pensjoneringen de radene som ennå ikke fantes.
        # `ON CONFLICT` kan ikke fange dem heller — for de mottakerne
        # forsoningen finnes for, er det nettopp den opprinnelige raden som
        # MANGLER. Resultatet var et ulest, e-postkøet varsel om en runde som
        # var ferdig, altså en ny løgn skapt av veien som skulle reparere en.
        #
        # Låsen holder til kallerens commit, så attesteringen kan ikke lukke
        # runden før varslene finnes — og finner den dem, pensjonerer den dem.
        #
        # Låsrekkefølgen kan ikke gi vranglås: attesteringen tar utkastet
        # først og runden etterpå, denne veien tar KUN runden og venter aldri
        # på utkastet. Ingen sirkel.
        laast = conn.execute(
            "SELECT status, utloper, pakrevd_antall_godkjennere"
            "  FROM aktiveringsrunde"
            " WHERE tenant=%s AND utkast_id=%s AND runde=%s FOR UPDATE",
            (tenant, utkast_id, runde)).fetchone()
        if laast is None:
            return None
        antall, ikke_forfatter = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE NOT er_forfatter)"
            "  FROM aktiveringsattestasjon"
            " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
            (tenant, utkast_id, runde)).fetchone()
        return (*laast, antall, ikke_forfatter)

    rad = varsel.skjermet(conn, _les_under_laas)
    if rad is varsel.FEILET or rad is None:
        return 0
    if _runde_status(rad[0], rad[1], naa) != "apen":
        return 0
    # Tallet leses NÅ, ikke fra det lagrede utfallet: forsoningen kjøres etter
    # at runden ble åpnet, og noen kan ha rukket å attestere i mellomtiden.
    # Med det lagrede påkrevd-tallet ville varselet som endelig kom frem sagt
    # at alle attestasjonene gjenstår — det ville vært det samme feiltrinnet
    # `oppdater_gjenstaar` finnes for, bare i den veien som skal REPARERE.
    # Og av samme grunn regnes det gjennom `_gjenstaar_effektivt`: har
    # forfatteren rukket å attestere i vinduet, er differansen 0 mens den
    # uavhengige godkjenneren fortsatt mangler.
    pakrevd = rad[2] if rad[2] is not None else lagret.get(
        "pakrevd_antall_godkjennere", 0)
    return varsel.varsle_runde_venter(
        conn, tenant=tenant, aktor=aktor, request_id=request_id,
        utkast_id=utkast_id, runde=int(runde),
        policy_id=lagret.get("policy_id", ""),
        risikoklasse=lagret.get("risikoklasse", ""),
        gjenstaar=_gjenstaar_effektivt(pakrevd, rad[3], rad[4]))


def _forson_rundepensjonering(conn: psycopg.Connection, tenant: str,
                              aktor: str, utkast_id: str, naa) -> int:
    """Rydd varsler som en best effort-pensjonering kan ha etterlatt.
    -> antall ryddet.

    Motstykket til `_forson_rundevarsling`, og av samme grunn (Codex P2):
    `pensjoner_runde` er skjermet med vilje — en fullmaktsendring skal ikke
    kunne velte fordi en opprydding gjorde det — men prisen var at feilen var
    ENDELIG. Traff en forbigående databasefeil oppryddingen, ble runden
    aktivert og committet likevel, og godkjennernes uleste, e-postkøede varsler
    ble stående og be dem attestere noe som var ferdig. Klientens retry kunne
    ikke reparere det: replay-grenen svarte med det lagrede utfallet FØR noen
    pensjonering ble forsøkt. Og senderen kan ikke fange det opp — den er
    kryss-tenant og vet med vilje ingenting om aktiveringsrunder, så e-posten
    gikk ut.

    TILSTANDEN, IKKE DET LAGREDE SVARET, avgjør hva som ryddes. Utfallene
    `rebasering_kreves` og `semantikk_endret` bærer ikke engang rundenummeret,
    så en forsoning som leste `lagret["runde"]` ville hoppet over nettopp de
    veiene der pensjoneringen er den eneste oppryddingen. Sannheten står i
    basen: en runde som ikke lenger venter, med varsler som fortsatt gjør det.

      * Runden er IKKE lenger åpen (brukt, kansellert, forfalt) → hele rundens
        varsler pensjoneres. Forfalt måles med `_runde_status`, samme predikat
        som skrive- og lesestien, så en runde som har passert `utloper` uten at
        noen har rukket å lukke raden regnes med.
      * Runden er fortsatt åpen, men AKTØREN har alt attestert → kun hennes
        egen rad. Det er steg 7c som kan ha feilet; de andre venter fortsatt,
        og deres varsler er sanne.

    EXISTS-leddet gjør dette gratis i normaltilfellet: står det ingenting
    igjen, finner spørringen ingen runder, og ingen UPDATE kjøres.

    Skjermet, som alt annet varslingsarbeid: en forsoning som velter replayen
    ville vært verre enn hullet den lukker.
    """
    def _kandidater():
        return conn.execute(
            "SELECT r.runde, r.status, r.utloper,"
            "       EXISTS (SELECT 1 FROM aktiveringsattestasjon a"
            "                WHERE a.tenant=r.tenant AND a.utkast_id=r.utkast_id"
            "                  AND a.runde=r.runde AND a.bruker_id=%s)"
            "  FROM aktiveringsrunde r"
            " WHERE r.tenant=%s AND r.utkast_id=%s"
            "   AND EXISTS (SELECT 1 FROM varsel v"
            "                WHERE v.tenant=r.tenant"
            "                  AND v.art='attestering_venter'"
            "                  AND v.ressurs_type='policyutkast'"
            "                  AND v.ressurs_id=r.utkast_id"
            "                  AND v.hendelse=r.runde::text"
            "                  AND (v.lest_ts IS NULL"
            f"                       OR v.epost_status IN {varsel.I_KO}))",
            (aktor, tenant, utkast_id)).fetchall()

    rader = varsel.skjermet(conn, _kandidater)
    if rader is varsel.FEILET or not rader:
        return 0
    ryddet = 0
    for runde, status, utloper, aktor_attesterte in rader:
        if _runde_status(status, utloper, naa) != "apen":
            ryddet += varsel.pensjoner_runde(
                conn, tenant=tenant, utkast_id=utkast_id, runde=runde)
        elif aktor_attesterte:
            ryddet += varsel.pensjoner_runde(
                conn, tenant=tenant, utkast_id=utkast_id, runde=runde,
                bruker_id=aktor)
    return ryddet


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
    # Runden er død; varselet om den skal ikke bli stående og be folk om å
    # attestere noe som ikke lenger kan attesteres (Codex P2).
    varsel.pensjoner_runde(conn, tenant=tenant, utkast_id=utkast_id, runde=r_nr)
    return False


def _kanseller_levende_runde(conn: psycopg.Connection, tenant: str,
                             utkast_id: str, naa) -> None:
    """Trekk utkastets ÅPNE runde tilbake, fordi eieren av forslaget trekker
    selve forslaget (gjenåpning eller forkasting).

    Runden er en FORESPØRSEL om godkjenning, ikke en fullmakt: å kansellere
    den gir ingen agent lov til noe mer eller mindre. Og etter handlingen som
    kaller hit kan runden uansett aldri lykkes — et gjenåpnet utkast mister
    `innholds_hash`-en runden er frosset mot, et forkastet utkast finnes ikke
    som forslag lenger. Å la runden stå «åpen» ville bedt godkjennere signere
    på noe som ikke kan aktiveres (nøyaktig tilstanden `_kanseller_runde`
    finnes for å avvikle). Eier sto i praksis fast i 24 timer (RUNDE_TTL) på
    et utkast han selv eide — seks slettinger på rad døde mot den samme
    runden 17/8 før dette ble en kodesti.

    En `klar` runde røres ALDRI her: da er utkastet `godkjent`, og både
    gjenåpning og forkasting avviser den statusen før de kommer hit — fire
    øyne som HAR sagt ja avvikles ikke som et forslag ingen har vurdert.
    Attestasjonene består uansett (append-only); de tilhører runden.

    Kalleren eier tx og holder utkastraden — samme låsrekkefølge som
    `_lukk_forfalt_runde` (utkast, så runde)."""
    if not _lukk_forfalt_runde(conn, tenant, utkast_id, naa):
        return                     # ingen levende runde — ingenting å trekke
    rad = conn.execute(
        "SELECT runde FROM aktiveringsrunde WHERE tenant=%s AND utkast_id=%s"
        " AND status='apen' FOR UPDATE", (tenant, utkast_id)).fetchone()
    if rad is None:
        return
    conn.execute(
        "UPDATE aktiveringsrunde SET status='kansellert' WHERE tenant=%s"
        " AND utkast_id=%s AND runde=%s", (tenant, utkast_id, rad[0]))
    varsel.pensjoner_runde(conn, tenant=tenant, utkast_id=utkast_id,
                           runde=rad[0])


def gjenapne_utkast(conn: psycopg.Connection, *, tenant: str, aktor: str,
                    request_id: str, utkast_id: str, forventet_utkastversjon,
                    idempotency_key: str, input_hash: str, naa) -> dict:
    """Gjenåpne et VALIDERT utkast for redigering: status `validert → utkast`,
    `innholds_hash` nullstilles (migrasjon 033), og en åpen runde trekkes
    tilbake (`_kanseller_levende_runde`).

    Eiers krav (17/8): «man må kunne redigere samme policy selv etter
    validering … men da kan den igjen bli attestert og validert.» Uten denne
    veien var et validert utkast med en feil en blindgate — eneste utvei var
    å forkaste alt og begynne på nytt, og en åpen runde sperret til og med
    forkastingen i 24 timer.

    Ingen snarvei rundt fire øyne: det gjenåpnede utkastet må valideres på
    nytt (ny hash-frysing) og gjennom en HELT NY runde med nye attestasjoner
    før noe aktiveres. `godkjent` avvises som i `forkast_utkast` — en
    godkjenning avvikles ikke ved å redigere den bort.

    Idempotensnøkkelen bindes til utkastversjonen som ellers i modulen.
    Kalleren eier tx."""
    sett_kontekst(conn, tenant, aktor, request_id)
    tilstand, lagret = _idempotent_start(conn, tenant, idempotency_key,
                                         input_hash, request_id)
    if tilstand == "replay":
        # Handlingen gjentas ikke — men PENSJONERINGEN forsones (samme
        # begrunnelse og mønster som attesteringens replay): kanselleringen av
        # runden er committet, mens varselryddingen er best effort og kan ha
        # feilet. Uten forsoningen her var retryen som skulle reparere det ute
        # av døra før noen opprydding ble forsøkt. `commit()`, ikke
        # `rollback()` — en forsoning som kastes på vei ut har ingen verdi,
        # og ingenting annet er skrevet (idempotens-INSERT traff DO NOTHING).
        _forson_rundepensjonering(conn, tenant, aktor, utkast_id, naa)
        conn.commit()
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
    if status != "validert":
        conn.rollback()
        raise Aktiveringsfeil("utkast_ulovlig_tilstand", f"status={status}")
    if not isinstance(forventet_utkastversjon, int) \
            or isinstance(forventet_utkastversjon, bool) \
            or forventet_utkastversjon != ver:
        conn.rollback()
        raise Aktiveringsfeil("utkastversjon_utdatert", f"er={ver}")
    _kanseller_levende_runde(conn, tenant, utkast_id, naa)
    # Versjonen BUMPES i selve overgangen (Codex P1): gjenåpningen gjør raden
    # redigerbar igjen, og hver editor som lastet utkastet FØR valideringen
    # holder fortsatt versjon N. Sto raden igjen på N, passerte en slik
    # foreldet editor både status- og versjonssjekken i `rediger_utkast` og
    # overskrev det gjenåpnede utkastet stille — nøyaktig det den optimistiske
    # låsen finnes for å hindre. Med N+1 er ALLE krav fra før valideringen
    # ugyldige; den som vil redigere må laste utkastet på nytt.
    ny = ver + 1
    conn.execute(
        "UPDATE policyutkast SET status='utkast', innholds_hash=NULL,"
        " utkastversjon=%s WHERE tenant=%s AND utkast_id=%s",
        (ny, tenant, utkast_id))
    return _fullfor(conn, tenant, idempotency_key, {
        "utkast_id": utkast_id, "status": "utkast", "utkastversjon": ny})


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

    Én ting nektes: `godkjent` — da HAR fire øyne sagt ja, og å kaste den
    godkjenningen er en annen handling enn å rydde bort et forslag ingen har
    vurdert.

    En ÅPEN runde nektes IKKE lenger — den trekkes tilbake
    (`_kanseller_levende_runde`). Den forrige regelen («runden må avsluttes
    først») fantes for å verne godkjennere mot at grunnlaget rives bort under
    dem, men den vernet ingen: et forkastet utkast kan uansett aldri
    aktiveres, så en «vernet» runde var bare en runde som ba folk signere på
    et dødt forslag — og eier, som selv eide både utkastet og runden, sto
    fast i opptil 24 timer (RUNDE_TTL) uten noen kodesti som kunne avslutte
    runden. Å trekke sitt eget forslag tilbake er forfatterens rett; ingen
    fullmakt endres av det, og attestasjonene består (append-only).

    Idempotensnøkkelen bindes til utkastversjonen som ellers i denne modulen.
    Kalleren eier tx.
    """
    sett_kontekst(conn, tenant, aktor, request_id)
    tilstand, lagret = _idempotent_start(conn, tenant, idempotency_key,
                                         input_hash, request_id)
    if tilstand == "replay":
        # Se gjenåpningens replay: kanselleringen av runden er committet, men
        # varselryddingen er best effort — forson den før svaret går ut.
        _forson_rundepensjonering(conn, tenant, aktor, utkast_id, naa)
        conn.commit()
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
    _kanseller_levende_runde(conn, tenant, utkast_id, naa)
    conn.execute(
        "UPDATE policyutkast SET status='forkastet'"
        " WHERE tenant=%s AND utkast_id=%s", (tenant, utkast_id))
    return _fullfor(conn, tenant, idempotency_key, {
        "utfall": "forkastet", "utkast_id": utkast_id})


def _lukk_forfalte_runder(conn: psycopg.Connection, tenant: str,
                          policy_id: str, naa) -> None:
    """Rydd policyens runder av veien for en bekreftet sletting: forfalte
    runder lukkes (`utlopt`), levende ÅPNE runder trekkes tilbake
    (`kansellert`, se løkka under) — begge før slettingen teller runder
    «i omløp». Kun `klar` (godkjent utkast) blir stående og blokkerer.

    Uten dette blokkerte en forfalt runde slettingen for alltid (Codex P2).
    Vernet i `slett_ubrukt_policy` teller `status IN ('apen','klar')` — den
    LAGREDE statusen — og den er ikke en løgn, bare foreldet: overgangen til
    `utlopt` skjer først når en skrivesti kommer forbi. Kom ingen forbi, sto
    runden `apen` i det uendelige, og en ubrukt policy med en runde ingen kan
    attestere (`attester_aktivering` nekter den med `runde_utlopt`) svarte
    `runde_allerede_aapen` hver eneste gang. Vilkåret «ingen attestasjoner i
    omløp» var oppfylt; det var bare ingen som hadde skrevet det ned.

    Overgangen kjøres, den utelates ikke i et predikat: `_lukk_forfalt_runde`
    er den ENE definisjonen av «forfalt» (`_runde_status`), og en fjerde kopi —
    denne gangen i SQL, med sin egen klokke — er nøyaktig det de tre andre
    veiene er skrevet for å unngå. Den pensjonerer dessuten varselet, så
    godkjennerne slutter å bli bedt om å attestere en runde som nå heller ikke
    har en policy å aktivere.

    LÅSREKKEFØLGEN er `opprett_aktiveringsrunde` sin: utkastraden, så runden,
    og FØRST DERETTER den eksklusive policylåsen inne i `slett_ubrukt_policy`.
    Motsatt vei ville laget en sirkel mot en runde-åpning som holder utkastet
    og venter på den delte låsen. Utkastene låses i utkast_id-rekkefølge, så to
    slettinger på samme policy heller ikke kan gå i ring.

    Kommer en NY runde til etter skanningen, er den ikke forfalt (en runde
    åpnes med `utloper` i framtiden) — og da SKAL den blokkere. Den telles av
    `slett_ubrukt_policy` under policylåsen, som er stedet det avgjøres.
    """
    rader = conn.execute(
        "SELECT DISTINCT r.utkast_id FROM aktiveringsrunde r"
        "  JOIN policyutkast u ON u.tenant=r.tenant AND u.utkast_id=r.utkast_id"
        " WHERE r.tenant=%s AND u.policy_id=%s AND r.status IN ('apen','klar')"
        " ORDER BY 1", (tenant, policy_id)).fetchall()
    for (utkast_id,) in rader:
        conn.execute("SELECT 1 FROM policyutkast WHERE tenant=%s"
                     " AND utkast_id=%s FOR UPDATE", (tenant, utkast_id))
        # Også LEVENDE åpne runder trekkes tilbake, ikke bare forfalte: eier
        # har nettopp bekreftet at policyen skal bort, og en runde som venter
        # på å aktivere den kan bare gjenskape det han fjerner. Seks slettinger
        # på rad døde 17/8 mot en levende runde på et ANNET utkast av samme
        # policy — en tilstand flaten verken viste eller kunne løse opp. En
        # `klar` runde (utkastet er `godkjent`) røres fortsatt ikke; den telles
        # av `slett_ubrukt_policy` under policylåsen og blokkerer med rette.
        _kanseller_levende_runde(conn, tenant, utkast_id, naa)


def slett_policy(conn: psycopg.Connection, *, tenant: str, aktor: str,
                 request_id: str, policy_id: str, forventet_versjon: str,
                 forventet_hash: str, idempotency_key: str,
                 input_hash: str, naa) -> dict:
    """Angre en feilopprettet policy: slett den som ALDRI har styrt en
    beslutning. Alle vilkårene håndheves av `slett_ubrukt_policy` (032) — her
    ligger idempotensen og OVERGANGEN som lukker forfalte runder.

    `forventet_versjon`/`forventet_hash` er den aktive policyen KLIENTEN SÅ, og
    de sendes videre urørt: sammenligningen hører hjemme under policylåsen inne
    i funksjonen (Codex P1), ikke i en lesning her ute som en aktivering kan gå
    forbi mellom lesningen og kallet. Avviker de, er policyen byttet ut under
    operatøren — `policy_endret`, ikke en sletting av noe hun aldri så.

    Idempotensen er ikke pynt på en `Idempotency-Key` endepunktet uansett
    krever (Codex P2). Slettingen er ENGANGS og irreversibel: går svaret tapt
    på veien tilbake — nettopp det retry-en finnes for — er policyen borte, og
    et nytt forsøk med samme nøkkel møtte `policy_ukjent`. Eier fikk da en
    endelig feilmelding på en operasjon som FAKTISK lyktes, ble stående på en
    flate som viste den slettede policyen som aktiv, og hvert nye forsøk sa det
    samme til hun lastet siden på nytt. Med claimet på plass svarer replayen
    NØYAKTIG det lagrede svaret, og flaten kommer videre.

    `naa` er klokka forfalte runder måles mot, som i `forkast_utkast` og
    `opprett_aktiveringsrunde` — se `_lukk_forfalte_runder` under.

    Kalleren eier tx; `_fullfor` committer.
    """
    sett_kontekst(conn, tenant, aktor, request_id)
    tilstand, lagret = _idempotent_start(conn, tenant, idempotency_key,
                                         input_hash, request_id)
    if tilstand == "replay":
        # Slettingen kansellerte policyens åpne runder; ryddingen av varslene
        # deres er best effort og kan ha feilet. Forson per utkast — samme
        # klasse som gjenåpningens og forkastingens replay, bare at policyen
        # kan ha flere utkast med runder.
        for (uid,) in conn.execute(
                "SELECT DISTINCT r.utkast_id FROM aktiveringsrunde r"
                "  JOIN policyutkast u ON u.tenant=r.tenant"
                "   AND u.utkast_id=r.utkast_id"
                " WHERE r.tenant=%s AND u.policy_id=%s ORDER BY 1",
                (tenant, policy_id)).fetchall():
            _forson_rundepensjonering(conn, tenant, aktor, uid, naa)
        conn.commit()
        return lagret
    if tilstand == "konflikt":
        conn.rollback()
        raise Aktiveringsfeil("idempotenskonflikt")
    _lukk_forfalte_runder(conn, tenant, policy_id, naa)
    try:
        n = conn.execute(
            "SELECT slett_ubrukt_policy(%s,%s,%s,%s)",
            (tenant, policy_id, forventet_versjon,
             forventet_hash)).fetchone()[0]
    except psycopg.errors.CheckViolation as e:
        # Rollback tar claimet med seg, som ellers i modulen: en operasjon som
        # ikke skjedde skal ikke brenne nøkkelen. Retry på en policy som er i
        # bruk gir samme forklaring hver gang — det er sannheten om tilstanden.
        conn.rollback()
        # Prøven står på RUNDEN, ikke på bruken. Vilkårsbruddene fra 032 er nå
        # tre, ikke to: styrt en beslutning, åpen runde, og en referanse
        # retensjonsvakta (V3) fant og funksjonen oversatte. Bare det midterste
        # har en egen forklaring på flaten; de to andre sier begge at policyen
        # er referert og må avvikles i stedet. Med testen på «beslutning» ville
        # den nye, oversatte avvisningen falt ut som `runde_allerede_aapen` —
        # en feilmelding om en runde som ikke finnes.
        raise Aktiveringsfeil(
            "runde_allerede_aapen" if "aktiveringsrunde" in str(e)
            else "policy_i_bruk") from None
    except psycopg.errors.InvalidParameterValue:
        # Den aktive policyen er ikke den klienten så. Rollback som over: en
        # sletting som ikke skjedde skal ikke brenne nøkkelen — flaten kan
        # laste på nytt og prøve igjen mot den versjonen som NÅ står.
        conn.rollback()
        raise Aktiveringsfeil("policy_endret") from None
    except psycopg.errors.NoDataFound:
        conn.rollback()
        raise Aktiveringsfeil("policy_ukjent") from None
    return _fullfor(conn, tenant, idempotency_key,
                    {"slettet": n, "policy_id": policy_id})


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
    """Utkastene for tenanten (evt. filtrert på policy_id). Rent lesende.

    Lista er ARBEIDSKØEN, ikke historikken (eier 17/8: «når man sletter en
    policy skal det fjernes helt fra listen»). To radtyper holdes derfor ute:

      * `forkastet` — et slettet forslag. Det er terminalt og har ingen
        handling igjen; å vise det for alltid var nøyaktig det eier meldte.
      * `aktivert` som IKKE er gjeldende tilstand: enten fordi policyen siden
        er slettet (raden sto igjen som «Aktivert» om en policy som ikke
        finnes), eller fordi en senere aktivering har avløst den. Det
        aktiverte utkastet som ER den aktive policyen vises fortsatt — det er
        gjeldende tilstand, ikke et lik.

    RADEN BINDES TIL GENERASJONEN, IKKE TIL NOE GJENBRUKBART (Codex P2, to
    runder). Prøven må svare på «ble dette utkastet den generasjonen som er
    aktiv NÅ», og de to nærliggende svarene er begge gjenbrukbare:

      * `policy_id` alene: en id blir LEDIG igjen etter sletting — 032
        nullstiller pekeren og frigjør versjonsnumrene, nettopp så en riktig
        opprettelse etterpå ikke stoppes av 020-monotonien. Aktiveres en
        erstatning under samme id, er pekeren ikke-NULL på nytt, og det
        SLETTEDE utkastet kom tilbake i køen som «gjeldende tilstand».
      * `innholds_hash`: 020 gjør versjonen til DOKUMENTETS versjon, og siden
        032 sletter radene kan nøyaktig samme dokument aktiveres om igjen.
        Hashen er deterministisk av innholdet, så erstatningen får da samme
        hash som den slettede generasjonen — og prøven ble sann for BEGGE.

    Begge er beskrivelser, og en beskrivelse kan passe på to generasjoner
    samtidig. Identiteten som ikke kan gjenbrukes er `policy_hode.revisjon`:
    en teller som bare går oppover, aldri nullstilt, på en ankerrad som aldri
    slettes. Tre veier skriver den, og alle tre teller OPP — den styrte
    aktiveringen, slettingen (032) og `policyregister.registrer` når den
    aktiverer (oppsett-/token-veien, som skriver `policyer` helt uten
    utkast). Den siste er også grunnen til at prøven er riktig konservativ:
    skrives en ny generasjon utenom utkastveien, er den aktive generasjonen
    ikke lenger den utkastet laget, og utkastet forsvinner fra køen. Migrasjon
    034 stempler utkastet med den telleren i det aktiveringen skjer
    (`aktivert_revisjon`, server-utledet av trigger), og prøven her er derfor
    en likhet mellom to tall: hodets revisjon er fortsatt den dette utkastet
    laget. Gjenbrukt id, gjenbrukt versjon og gjenbrukt innhold gjør den ikke
    sann for en generasjon som er borte.

    `aktiv_versjon IS NOT NULL` står ved siden av likheten, ikke i stedet for
    den: en sletting bumper også telleren, så likheten alene ville holdt — men
    den dagen en ny vei fjerner en policy uten å røre `revisjon`, er det denne
    linjen som gjør at et lik ikke vises. Pekeren kan leses her (og ikke bare
    `policyer`-raden, slik 032 må gjøre) fordi hvert `aktivert` utkast har
    gått gjennom `aktiver_policy`, som oppretter hoderaden før den rører
    utkastet: en grandfathered policy uten hoderad har ingen utkast å skjule.

    Et AVLØST aktivert utkast faller dermed også ut, og det er riktig for en
    arbeidskø: `aktivert` er terminalt (statusmaskinen i 012/033), så raden
    har ingen handling igjen uansett.

    Radene finnes fremdeles i basen — de er attestasjonshistorikkens ankre
    (slettingen i 032 bevarer dem uttrykkelig) og nås via detalj-ruten.
    Skjulingen er en visningsregel, ingen sletting."""
    sett_kontekst(conn, tenant, aktor, request_id)
    vilkaar = (" AND u.status <> 'forkastet'"
               " AND (u.status <> 'aktivert' OR EXISTS ("
               "      SELECT 1 FROM policy_hode h WHERE h.tenant=u.tenant"
               "        AND h.policy_id=u.policy_id"
               "        AND h.aktiv_versjon IS NOT NULL"
               "        AND h.revisjon=u.aktivert_revisjon))")
    if policy_id:
        rows = conn.execute(
            "SELECT u.utkast_id, u.policy_id, u.status, u.utkastversjon,"
            " u.opprettet FROM policyutkast u WHERE u.tenant=%s"
            " AND u.policy_id=%s" + vilkaar +
            " ORDER BY u.opprettet DESC", (tenant, policy_id)).fetchall()
    else:
        rows = conn.execute(
            "SELECT u.utkast_id, u.policy_id, u.status, u.utkastversjon,"
            " u.opprettet FROM policyutkast u WHERE u.tenant=%s" + vilkaar +
            " ORDER BY u.opprettet DESC", (tenant,)).fetchall()
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
    hoderad).

    Men mot SLETTING (`slett_ubrukt_policy`, 032) holdt det ikke å ikke låse
    (Codex P2). Slettingen lover at den ikke etterlater attestasjoner i omløp,
    og kontrollerer det ved å telle åpne runder. En naken SELECT her lot
    runde-åpningen validere basen, slettingen telle null runder og committe, og
    så runde-INSERT-en lande på en policy som ikke lenger finnes — godkjennere
    sendt inn i en runde som aldri kan aktiveres. Den delte låsen på policyen
    holder til denne transaksjonen committer, altså til runden STÅR, og
    slettingens eksklusive lås på samme nøkkel ser den. Radlås er ikke et
    alternativ: `FOR SHARE` krever UPDATE-privilegium, som runtime ikke har.
    """
    laas_policy_delt(conn, tenant, policy_id)
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
#:
#: Måles med `fullmatch` av samme grunn som `_SEMVER`: `"acme\n"` er ikke en
#: skjemagyldig id, men Pythons `$` godtar halen.
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
    `_krev_produksjonsstatus`) og databasens (migrasjon 023/024) — samlet her
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
    if isinstance(versjon, str) and not _SEMVER.fullmatch(versjon):
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


#: `aktiver_policy` reiser innholdsinvariantene sine som `check_violation` og
#: NAVNGIR bruddet (`USING CONSTRAINT`). Navnet er det eneste som skiller dem
#: fra hverandre i feilen kalleren ser — uten det ville et identitetsavvik blitt
#: rapportert som `versjon_i_bruk`: riktig kansellering, feil beskjed til eier.
#: Ukjent/uten navn → versjonsinvariantene fra 020, som er de eldste.
#:
#: `verifikator_id_entydig` (migrasjon 022) hører hjemme i samme tabell selv om
#: den kom en annen vei: den deler SQLSTATE med de andre og krever sin egen
#: retting av eier. Én tabell, ikke en tabell og et unntak ved siden av.
#: `policyref_lesbar` (migrasjon 025) er samme sak én gang til: et framoverrettet
#: krav fra innføringskontrakten, speilet i SQL fordi Python-porten kan være
#: passert før utrullingen. Eier retter det på samme måte, så koden er den samme.
_DOKUMENTBRUDD = {"dokument_policy_id": "dokument_avvik",
                  "dokument_status": "dokument_avvik",
                  "verifikator_id_entydig": "utkast_ugyldig",
                  "policyref_lesbar": "utkast_ugyldig"}

#: Skjemaets versjonsform (`policy-schema-v0.2.json`: `meta.versjon`), men med
#: ASCII-sifre EKSPLISITT (Codex P2). Pythons `\d` matcher hele Unicodes
#: desimalsiffer-kategori, og det gjør `jsonschema` også — så «١.٠.٠» er
#: skjemagyldig. Databasen bruker `[0-9]` (migrasjon 020–025) og avviser den, og
#: nøkkelen under sammenligner sifrene som TEKST, der «١» sorterer over «2».
#: Uten dette godtok porten altså en versjon som er både feilordnet og
#: ulagringsbar, åpnet runden, og lot bruddet komme etter attestasjonene — med
#: en kansellert runde som resultat. De to gatene skal måle det samme.
#:
#: Måles med `fullmatch`, ikke `match` (Codex P2). `$` er ikke slutten på
#: strengen i Python — den matcher også rett før en avsluttende linjeskift — så
#: `"1.2.3\n"` slapp gjennom både her og skjemaet, mens migrasjonenes `$` leser
#: den som ekte slutt og avviser den. Samme sykdom som unicode-sifrene over,
#: samme utfall: frosset og attestert utkast, brudd først i aktiveringen,
#: kansellert runde. Skjemasiden er lukket i `policy_validator.schema`
#: (`_ecma_ankre`); `fullmatch` her gjør porten uavhengig av hva `re` mener om
#: ankrene. Ankrene i mønsteret beholdes fordi det er en KOPI av skjemaets.
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


def nytt_utkast_avvik(tenant: str, policy_id: str) -> tuple[str, str] | None:
    """`(kode, detalj)` for grunnen til at et NYTT utkast på denne identiteten
    ville blitt avvist ved opprettelsen — eller `None` om den kan bære et.

    ÉN definisjon, to lesere (Codex P2). `opprett_utkast` bruker den som port,
    og HISTORIKKEN bruker den til å avgjøre om rullbakk i det hele tatt er en
    mulig handling for serien. Lastekontrakten slipper med vilje gjennom
    aktive policyer fra før innstrammingen — en arvet id som `acme\\n` finnes
    og skal fortsatt kunne LESES — men en slik serie kan ikke få et nytt
    utkast. Tilbød flaten rullbakk likevel, endte hver eneste knapp i et 400
    ingen kunne gjøre noe med. Med to kopier av prøven ville flaten før eller
    siden tilbudt en knapp porten avviser, eller skjult en den godtar.

    FORMEN først: en id som ikke er skjemagyldig kan aldri skrives inn i
    dokumentet, og en skjemagyldig id ville spriket fra raden. Rekkefølgen er
    ikke tilfeldig — `"ACME"` er feil FORM, ikke for stor, og skal få den
    beskjeden.

    Så PLASSEN: levner identiteten ikke rom til en versjon i registerets
    primærnøkkel, er ingen versjon eier senere kan skrive i stand til å få
    plass. Da er det opprettelsen som skal si nei, ikke en validering hun
    aldri kan tilfredsstille.

    EGEN KODE, ikke `utkast_feilformet` (Codex P3). HTTP-laget slipper bare
    koden videre — detaljen når aldri skjermen — og editoren oversetter
    `utkast_feilformet` til «innholdet er ikke gyldig JSON-struktur». Eier ble
    altså sendt for å reparere dokumentet sitt, som er helt i orden, mens det
    eneste som må gjøres er å FORKORTE id-en. En id som er for stor er heller
    ikke feil form: `_POLICY_ID` har alt sagt ja til den."""
    if not _POLICY_ID.fullmatch(policy_id or ""):
        return ("policy_id_ugyldig", f"policy_id={policy_id!r}")
    stor = _nokkelbytes(tenant, policy_id)
    if stor > _MAKS_NOKKELBYTES - _VERSJONSRESERVE:
        return ("policy_id_for_stor",
                "policy_id levner ikke plass til en versjon i"
                f" registernøkkelen ({stor} byte)")
    return None
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
    if not isinstance(ny, str) or not _SEMVER.fullmatch(ny) \
            or _nokkelbytes(tenant, policy_id, ny) > _MAKS_NOKKELBYTES:
        # Formen OG plassen: en nøkkel registeret ikke kan lagre er like umulig
        # å aktivere som en versjon som mangler (se `_MAKS_NOKKELBYTES`), og
        # skal stoppes her — ikke som en indeksfeil etter to signaturer.
        raise Aktiveringsfeil("versjon_mangler", f"meta.versjon={ny!r:.80}")
    # STATUSKRAVET FRA #67 BÆRES VIDERE, men det bor et annet sted her.
    # `main` la det inne i denne funksjonen fordi den ikke hadde noen annen
    # felles port; denne grenen har `_krev_produksjonsstatus`, som kalles rett
    # FØR dette kallet på begge veiene (runde-åpning og attestering) og har
    # migrasjon 024 som siste skanse. En kopi her ville aldri kunnet fyre — den
    # ville stått som en invariant ingen test kunne felle. Invarianten er den
    # samme, og den slår til på nøyaktig samme tidspunkt.
    if conn.execute(
            "SELECT 1 FROM policyer WHERE tenant=%s AND policy_id=%s"
            " AND versjon=%s", (tenant, policy_id, ny)).fetchone():
        raise Aktiveringsfeil("versjon_i_bruk", f"versjon={ny} finnes")
    if aktiv_versjon is not None and _TALLVERSJON.fullmatch(aktiv_versjon):
        ledd = max(ny.count("."), aktiv_versjon.count(".")) + 1
        if _versjonsnokkel(ny, ledd) <= _versjonsnokkel(aktiv_versjon, ledd):
            raise Aktiveringsfeil(
                "versjon_i_bruk",
                f"versjon={ny} ikke nyere enn {aktiv_versjon}")
    return ny


def _neste_ledige_versjon(conn, tenant: str, policy_id: str,
                          aktiv_versjon: str | None) -> str | None:
    """Det minste versjonsnummeret som er STØRRE enn alt registeret kjenner:
    siste ledd i den høyeste kjente versjonen, pluss én, hoppende over
    eventuelle registrerte kollisjoner. -> None når ingenting er kjent (da
    finnes det ingen kollisjon å foreslå seg forbi).

    Sammenligningen bruker `_versjonsnokkel` med samme ledd-utvidelse som
    `_krev_ny_versjon` — én forståelse av «nyere», ikke to."""
    kjente = [r[0] for r in conn.execute(
        "SELECT versjon FROM policyer WHERE tenant=%s AND policy_id=%s",
        (tenant, policy_id)).fetchall()]
    if isinstance(aktiv_versjon, str):
        kjente.append(aktiv_versjon)
    kjente = [v for v in kjente if isinstance(v, str) and _SEMVER.fullmatch(v)]
    if not kjente:
        return None
    ledd = max(v.count(".") for v in kjente) + 1
    topp = max(kjente, key=lambda v: _versjonsnokkel(v, ledd))
    deler = topp.split(".")
    opptatt = set(kjente)
    while True:
        deler[-1] = str(int(deler[-1]) + 1)
        kandidat = ".".join(deler)
        if kandidat not in opptatt and not conn.execute(
                "SELECT 1 FROM policyer WHERE tenant=%s AND policy_id=%s"
                " AND versjon=%s", (tenant, policy_id, kandidat)).fetchone():
            return kandidat


def _versjonsavvik(conn, tenant: str, policy_id: str, innhold) -> list[str]:
    """`meta.versjon` målt mot registeret, som TEKST eier kan handle på —
    valideringens søsken til `_dokumentavvik`, men med databasen i hånden.

    Prøven ER porten: `_krev_ny_versjon` kjøres og utfallet oversettes, så
    valideringen og rundeåpningen aldri kan mene noe ulikt om samme versjon.
    Formfeil (ikke-semver) rapporteres alt av `_dokumentavvik` og gjentas
    ikke her."""
    versjon = _meta(innhold).get("versjon")
    if not isinstance(versjon, str) or not _SEMVER.fullmatch(versjon):
        return []
    aktiv = _hode_aktiv_versjon(conn, tenant, policy_id)
    try:
        _krev_ny_versjon(conn, tenant, policy_id, innhold, aktiv)
        return []
    except Aktiveringsfeil:
        forslag = _neste_ledige_versjon(conn, tenant, policy_id, aktiv)
        rad = (f"meta.versjon {versjon} er versjonen som er aktiv nå"
               if versjon == aktiv
               else f"meta.versjon {versjon} er allerede registrert eller"
                    f" ikke høyere enn den aktive ({aktiv or 'ingen'})")
        if forslag:
            rad += f" — sett en høyere versjon, for eksempel {forslag}"
        return [rad + ". En aktivering lagrer utkastets egen meta.versjon,"
                " så den må være ny og høyere enn den aktive"]


def _typens_sideeffektklasse(conn, oppdragstype: str) -> str | None:
    """Sideeffektklassen til kontrakten som EIER oppdragstypen — join på
    hele identiteten (eiermodul, kontraktversjon, kontrakt_hash), ikke bare
    modulen. -> None når typen ikke er registrert.

    Codex P2: en modulbred prøve leser feil rad. Kontraktrader er
    immutable og blir stående for alltid, så en modul som EN GANG hadde en
    `ekstern_lesing`-kontrakt bærer den videre — og en modulbred `LIMIT 1`
    ville klassifisert HVER handling for den modulen som ekstern lesing,
    også de som nå tilhører en nyere `sideeffektfri`-kontrakt. Følgen var
    ikke bare en unødvendig port: slike moduler kunne ikke lenger aktivere
    ellers gyldige policyer uten frekvens- og målautorisasjonsfelter som
    ikke hører hjemme der."""
    rad = conn.execute(
        "SELECT k.sideeffektklasse FROM oppdragstype_register r"
        "  JOIN modulkontrakt k ON k.modul_id = r.eiermodul"
        "   AND k.kontraktversjon = r.kontraktversjon"
        "   AND k.kontrakt_hash = r.kontrakt_hash"
        " WHERE r.oppdragstype = %s", (oppdragstype,)).fetchone()
    return rad[0] if rad else None


def _er_ekstern_lesing(conn, h) -> tuple[bool, object | None]:
    """Er DENNE handlingen ekstern lesing? -> (ja/nei, kodefestet type).

    Den ENE autoritative klassifiseringen, delt av aktiveringsporten
    (`_krev_ekstern_lesing_port`) og valideringen
    (`_krev_malautorisasjonsvilkar`) (Codex P2). To kopier av regelen var
    to steder å bli uenige, og uenigheten hadde en retning: valideringen
    var den mildeste, så et utkast kunne fryses som gyldig og først dø i
    porten — etter at innholdet ikke lenger kunne rettes.

    Rekkefølgen er KODEN FØRST. Bærer den kodefestede typen
    `krever_malautorisasjon`, ER dette ekstern lesing uansett hva
    `modulkontrakt` sier — også når registeret ikke har rukket å si noe
    (Codex P1) og også når det sier `sideeffektfri` (Codex P2, runde 19).
    En registrering kan legge krav TIL, aldri fjerne et krav koden
    stiller.

    Sier koden ingenting, avgjør registeret: typens EGEN kontrakt
    (`_typens_sideeffektklasse`, join på hele identiteten) når typen er
    registrert, ellers den konservative modulbrede prøven på den
    DEKLARERTE eiermodulen. `handlinger[].modul` er policyens navnerom
    (`M-23`) og kan bare brukes når koden ikke kjenner typen i det hele
    tatt."""
    import oppdragskontrakt
    hid = h.get("id") if isinstance(h.get("id"), str) else ""
    t = oppdragskontrakt.type_for_handling(hid)
    klasse = _typens_sideeffektklasse(conn, t.navn) if t is not None else None
    if t is not None and t.krever_malautorisasjon:
        return True, t
    if klasse is None:
        eier = t.eiermodul if t is not None and t.eiermodul else h.get("modul")
        if not isinstance(eier, str) or not conn.execute(
                "SELECT 1 FROM modulkontrakt WHERE modul_id=%s"
                " AND sideeffektklasse='ekstern_lesing' LIMIT 1",
                (eier,)).fetchone():
            return False, t
        return True, t
    return klasse == "ekstern_lesing", t


def _krev_ekstern_lesing_port(conn, ny_innhold) -> None:
    """Aktiveringsporten for `ekstern_lesing` (PR-014c §6) — under
    aktiveringslåsen, på begge veiene (rundeåpning og attestering, som
    `_krev_innforingskrav`): en handling hvis OPPDRAGSTYPE eies av en
    `ekstern_lesing`-kontrakt (`_typens_sideeffektklasse`) — ELLER hvis
    kodefestede type krever målautorisasjon, uansett hva registeret sier;
    sier registeret ingenting og koden stiller ikke kravet, faller vi
    konservativt tilbake på den deklarerte eiermodulen — kan bare aktiveres
    når

      1. `grenser.frekvens` er satt (observerbar trafikk ut skal alltid ha
         et tak policyen selv bærer),
      2. frekvensen grupperes på `ressurs_id` — altså på MÅLET, og
      3. handlingens vilkår inneholder minst ETT som har rad i
         `malautorisasjonsvilkar` med `maldomene` lik oppdragstypens
         `malautorisasjonsdomene`.

    Alle tre er POSITIVE krav. `krever_malautorisasjon: true` i den kodefestede
    typen uttrykker et behov, ikke et bevis — ukjent vilkårstype, manglende
    rad eller feil måldomene avviser aktiveringen. Fail-closed også når
    handlingen ikke matcher noen målautorisasjonsbærende type: da finnes
    det ikke noe vilkår som KAN telle, og en ekstern_lesing-handling uten
    autorisasjonsbegrep skal ikke gjennom fire øyne på flaks.

    HVILKE handlinger porten gjelder for leses av KODEN FØRST, ikke av
    registeret. Bærer den kodefestede typen `krever_malautorisasjon`, kjøres
    porten uansett hva `modulkontrakt` sier om klassen — også når registeret
    ikke har rukket å si noe, og også når det sier `sideeffektfri`. Bare der
    koden IKKE stiller kravet får registeret avgjøre, og da konservativt.
    Retningen er hele poenget: en registrering kan legge krav TIL, aldri
    fjerne et krav koden stiller.

    Krav 2 er det som gjør krav 1 til et TAK (Codex P2). Motoren i
    `policy_validator` teller per `event[grupperingsnokkel]`, og
    grupperingsnøkkelen er et feltnavn fra tenantens eget payload-domene:
    peker den på noe innsenderen kan variere fritt — en forespørsels-id,
    en tidsstempelstreng — får hver eneste forespørsel sin egen bøtte, og
    grensen «10 per time» blir «ubegrenset per time» mot ETT og samme
    nettsted. Da er den obligatoriske frekvensporten ren seremoni i
    nøyaktig den trafikken den ble innført for å begrense.

    `ressurs_id` er det ene feltet som IKKE er fritt: for en
    målautorisasjonsbærende type krever `malbindingsbrudd` at
    `event["ressurs_id"]` ER det normaliserte vertsnavnet i målfeltet, og
    attestasjonen bærer samme verdi inne i de signerte bytene. Bøtta
    følger derfor målet, ikke innsenderens fantasi. Kravet stilles kun
    for typer som faktisk bærer den bindingen — for andre finnes det
    ingen server-bundet nøkkel å kreve, og da ville kravet vært pynt.

    Vilkåret står i policyen fordi det gjør kravet synlig og reviewbart —
    men plattformregelen her gjelder uansett og kan ikke fjernes med fire
    øyne (014b §4-mønsteret: håndhevingen bor hos plattformen).
    """
    import oppdragskontrakt
    for h in (ny_innhold.get("handlinger") or []):
        if not isinstance(h, dict):
            continue
        hid = h.get("id") if isinstance(h.get("id"), str) else ""
        # Klassifiseringen bor i `_er_ekstern_lesing` — KODEN FØRST, med
        # registeret som konservativ reserve. Den er delt med valideringen
        # (`_krev_malautorisasjonsvilkar`) nettopp fordi to kopier av regelen
        # var to steder å bli uenige, og valideringen var den mildeste av dem
        # (Codex P2): et utkast med en kodefestet `krever_malautorisasjon`-type
        # og en manglende eller feilregistrert kontraktrad ble frosset som
        # gyldig og døde først her, etter at innholdet ikke lenger kunne
        # rettes. Se docstringen der for hvorfor retningen — registeret kan
        # legge krav TIL, aldri fjerne et krav koden stiller — er hele poenget.
        ekstern, t = _er_ekstern_lesing(conn, h)
        if not ekstern:
            continue
        grenser = h.get("grenser") if isinstance(h.get("grenser"), dict) \
            else {}
        if not isinstance(grenser.get("frekvens"), dict):
            raise Aktiveringsfeil("ekstern_lesing_uten_frekvens",
                                  f"handling={hid or '?'}")
        if t is None or not t.krever_malautorisasjon \
                or t.malautorisasjonsdomene is None:
            raise Aktiveringsfeil(
                "malautorisasjon_mangler",
                f"handling={hid or '?'}: ingen målautorisasjonsbærende"
                " oppdragstype")
        # Frekvensen må telle PER MÅL (Codex P2). Se docstringen: en fritt
        # valgt grupperingsnøkkel gir én bøtte per forespørsel, og da er
        # taket over ingen grense mot det nettstedet det gjelder.
        if grenser["frekvens"].get("grupperingsnokkel") != \
                oppdragskontrakt.MALBINDINGSFELT:
            raise Aktiveringsfeil(
                "frekvens_uten_malbinding",
                f"handling={hid or '?'}: grupperingsnokkel må være"
                f" {oppdragskontrakt.MALBINDINGSFELT}")
        navn = [v.get("navn") for v in (h.get("vilkaar") or [])
                if isinstance(v, dict) and isinstance(v.get("navn"), str)]
        navn += [v for v in (h.get("vilkaar") or []) if isinstance(v, str)]
        if not navn or conn.execute(
                "SELECT 1 FROM malautorisasjonsvilkar WHERE"
                " vilkar_type = ANY(%s) AND maldomene = %s LIMIT 1",
                (navn, t.malautorisasjonsdomene)).fetchone() is None:
            raise Aktiveringsfeil("malautorisasjon_mangler",
                                  f"handling={hid or '?'}")


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
    DB er invarianten som holder også for et direkte kall utenom oss.

    Den STRENGE varianten brukes her (Codex P2): svarer validatoren «intern
    feil» — skjemafilen mangler i en halvlandet utrulling, f.eks. — er det ikke
    en dom over utkastet, og det skal ikke se ut som en. Kalleren kansellerer
    runden på `utkast_ugyldig`, og en runde kansellert mens utrullingen var
    halvveis kommer ikke tilbake når filen gjør det."""
    try:
        feil = _schema.valider_innforingskrav_strengt(ny_innhold)
    except _schema.ValideringUtilgjengelig as e:
        raise Aktiveringsfeil("valideringsfeil_intern", str(e)) from e
    if feil:
        raise Aktiveringsfeil("utkast_ugyldig", "; ".join(feil))


#: Feilkodene som betyr at det FROSNE dokumentet er dømt: innholdet kan ikke
#: rettes, bare erstattes, så en runde som møter dem er beviselig død og skal
#: lukkes med det samme (`_kanseller_runde`). Tillatelsesliste, ikke
#: unntaksliste — en ny kode er trygg i den andre grenen (rull tilbake, la
#: runden leve) helt til noen har vist at den er permanent. `aktiv_peker_usynk`
#: og `valideringsfeil_intern` står bevisst UTENFOR: begge er reparerbare, og
#: den ene har dessuten en gjenopptakelsesvei (steg 7b).
_FROSNE_DOKUMENTBRUDD = frozenset({
    "policy_id_avvik", "status_ikke_produksjon",
    "versjon_i_bruk", "versjon_mangler", "utkast_ugyldig"})

#: `CONSTRAINT`-navnet `aktiver_policy` merker innføringskravbruddet med
#: (migrasjon 022). Skiller det fra versjonsinvariantene, som deler SQLSTATE
#: `check_violation` — uten det måtte utfallet utledes av feilteksten.
#: Utfallet slås opp i `_DOKUMENTBRUDD` sammen med de andre navngitte bruddene;
#: navnet står igjen her fordi testene binder migrasjonen til det.
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
        # Replayen gjentar ikke handlingen — runden er åpnet, og svaret er
        # nøyaktig det lagrede. Men den FORSONER varslingen (Codex P2): den er
        # best effort, så den kan ha feilet mens runden ble committet, og fram
        # til nå var det en endelig feil. Retryen som skulle reparert det, gikk
        # ut her uten å prøve. Varslingen er idempotent, så dette oppretter kun
        # det som faktisk mangler.
        #
        # Derfor `commit()` og ikke `rollback()`: forsoningen har ingen verdi
        # hvis den kastes på vei ut. Ingenting annet er skrevet i denne
        # transaksjonen — idempotens-INSERT-en traff `DO NOTHING`, og
        # advisory-låsen slippes likt av begge.
        _forson_rundevarsling(conn, tenant, aktor, request_id, lagret, naa)
        conn.commit()
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
    # Og ingen runde åpnes på et utkast som ikke KAN lagres: identiteten det
    # bærer må være radens egen (migrasjon 023), statusen må være den
    # aktiveringen skriver (migrasjon 024), og versjonen må være semantisk,
    # ubrukt og nyere enn den aktive (migrasjon 020).
    _krev_dokumentidentitet(policy_id, ny_innhold)
    _krev_produksjonsstatus(ny_innhold)
    _krev_ny_versjon(conn, tenant, policy_id, ny_innhold, aktiv_versjon)
    # ... eller som ikke oppfyller de framoverrettede kravene: `validert` kan
    # stamme fra før kravet fantes, og status alene er ingen kvittering.
    _krev_innforingskrav(ny_innhold)
    _krev_ekstern_lesing_port(conn, ny_innhold)
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

    # Si fra til dem som kan bringe runden videre. En åpen runde venter på et
    # MENNESKE, og fram til nå fikk hun aldri vite det — i praksis måtte eier
    # si fra utenom systemet. Varselet skrives i SAMME transaksjon som runden:
    # committes runden, finnes varselet; rulles runden tilbake, gjør varselet
    # det også. En egen transaksjon kunne etterlatt et varsel om en runde som
    # aldri ble åpnet.
    #
    # `varsle_runde_venter` kaster aldri og verner denne transaksjonen med en
    # savepoint. Det er med vilje og går én vei: en fullmaktsendring skal ikke
    # kunne feile fordi varslingen gjorde det. Konsekvensen av en varslingsfeil
    # er at et menneske ikke får en påminnelse — ikke at styringen stopper.
    varsel.varsle_runde_venter(
        conn, tenant=tenant, aktor=aktor, request_id=request_id,
        utkast_id=utkast_id, runde=runde, policy_id=policy_id,
        risikoklasse=v["risikoklasse"],
        gjenstaar=v["pakrevd_antall_godkjennere"])

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
        # Handlingen gjentas ikke — attestasjonen er skrevet og svaret er det
        # lagrede. Men PENSJONERINGEN forsones (Codex P2): den er best effort,
        # så den kan ha feilet mens runden ble aktivert og committet, og fram
        # til nå var det en endelig feil — replayen svarte her uten å prøve.
        # Uleste, e-postkøede varsler ble da stående og be om en attestering
        # som var ferdig, og senderen kan ikke se det: den vet med vilje
        # ingenting om runder.
        #
        # `commit()` og ikke `rollback()`, som i åpningens replay: en forsoning
        # som kastes på vei ut har ingen verdi. Ingenting annet er skrevet i
        # denne transaksjonen — idempotens-INSERT-en traff `DO NOTHING`.
        _forson_rundepensjonering(conn, tenant, aktor, utkast_id, naa)
        conn.commit()
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
    except Aktiveringsfeil:
        # Pekeren er det ENE som kan repareres uten et nytt utkast, og runden
        # skal derfor overleve: steg 7b lar den fullføres når eier har rettet
        # dataene. Kansellerte vi her, rev vi bort den veien.
        conn.rollback()
        raise

    # Så kravene på DOKUMENTET. Samme argument som for basen: en signatur på et
    # utkast som ikke kan lagres — eller som ville blitt lagret under en ANNEN
    # policy enn den det selv oppgir, eller som en policy det ikke sier at det
    # er — er like verdiløs som en signatur på feil base.
    try:
        _krev_dokumentidentitet(policy_id, ny_innhold)
        _krev_produksjonsstatus(ny_innhold)
        _krev_ny_versjon(conn, tenant, policy_id, ny_innhold, aktiv_versjon)
        # Og for de framoverrettede kravene: runden kan ha vært åpen da
        # utrullingen som innførte dem landet.
        _krev_innforingskrav(ny_innhold)
        _krev_ekstern_lesing_port(conn, ny_innhold)
    except Aktiveringsfeil as e:
        # RUNDEN KANSELLERES, den kastes ikke bare ut av (Codex P2). Alle fire
        # kravene her måler det FROSNE innholdet, så et avslag er permanent:
        # verken versjonen, identiteten, statusen eller en verifikator-id kan
        # rettes uten et nytt utkast og nye signaturer. Rullet vi bare tilbake,
        # ble runden stående `apen` og så levende ut — og eier sto fast. Flaten
        # tilbyr bare «attester» på en åpen runde, hvert forsøk gir samme feil,
        # og `forkast_utkast` nekter et utkast med en LEVENDE runde. Da var det
        # ingen vei ut før runden utløp av seg selv.
        #
        # Aktiveringsveien har gjort nøyaktig dette hele tiden
        # (`_kanseller_runde` på `check_violation` fra `aktiver_policy`); det
        # som manglet var at attesteringen — som møter de samme bruddene
        # FØRST — gjorde det samme. Signaturene som alt er avgitt består
        # (append-only), og ingen ny skrives: en signatur på et utkast som ikke
        # kan aktiveres er ingen godkjenning.
        #
        # Utfallet er feilkoden selv, så eier får vite hva som må rettes — som
        # `_DOKUMENTBRUDD` gjør det på aktiveringsveien.
        #
        # ... men bare for de kodene som FAKTISK er en dom over det frosne
        # dokumentet (Codex P2). En kontroll her kan også feile fordi VI er nede
        # — `valideringsfeil_intern` når skjemafilen ikke kan leses i en
        # halvlandet utrulling. Da vet vi ingenting om innholdet, og en runde
        # kansellert på den påstanden kommer ikke tilbake når filen gjør det.
        # Lista er derfor en tillatelsesliste og ikke en unntaksliste: en kode
        # som legges til senere havner i den TRYGGE grenen til noen har tenkt
        # gjennom om den er permanent.
        if e.kode not in _FROSNE_DOKUMENTBRUDD:
            conn.rollback()
            raise
        return _kanseller_runde(conn, tenant, idempotency_key, utkast_id,
                                policy_id, r_nr, e.kode)

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

    # --- 7c. Aktørens EGET varsel er ferdig -------------------------------
    # Hun har attestert; oppfordringen «venter på deg» er ikke sann lenger,
    # uansett om runden fortsatt venter på ANDRE. I den vanligste flyten
    # attesterer forfatteren rett etter at runden ble åpnet, og uten dette ville
    # hennes egen innboks bedt henne om å gjøre det hun nettopp gjorde
    # (Codex P2). Kun HENNES rad — de andre godkjennerne venter fortsatt.
    varsel.pensjoner_runde(conn, tenant=tenant, utkast_id=utkast_id,
                           runde=r_nr, bruker_id=aktor)

    # --- 8. Terskel (V6): antall ≥ påkrevd OG minst én ikke-forfatter -------
    rader = conn.execute(
        "SELECT bruker_id, er_forfatter, rolle, authz_version FROM"
        " aktiveringsattestasjon WHERE tenant=%s AND utkast_id=%s AND runde=%s",
        (tenant, utkast_id, r_nr)).fetchall()
    antall = len(rader)
    ikke_forfatter = sum(1 for _b, ef, _r, _a in rader if not ef)
    if antall < r_pakrevd or ikke_forfatter < 1:
        # Det samme tallet inn i varslene til dem som fortsatt venter (Codex
        # P2). Før ble det regnet ut BARE for dette svaret — altså bare for
        # den som nettopp attesterte og dermed er ferdig — mens varselet til
        # den som faktisk skal handle sto igjen med tallet fra åpningen og
        # sa «2 gjenstår» når bare hans egen sto igjen. E-posten sa det samme:
        # den rendres fra de samme parametrene, ved sending.
        gjenstaar = _gjenstaar_effektivt(r_pakrevd, antall, ikke_forfatter)
        varsel.oppdater_gjenstaar(conn, tenant=tenant, utkast_id=utkast_id,
                                  runde=r_nr, gjenstaar=gjenstaar)
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "venter_godkjennere", "utkast_id": utkast_id,
            "runde": r_nr, "antall": antall,
            "gjenstaar": gjenstaar,
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
        varsel.pensjoner_runde(conn, tenant=tenant, utkast_id=utkast_id,
                               runde=r_nr)
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "rebasering_kreves", "utkast_id": utkast_id})
    if (v["klassifikatorversjon"] != r_klassver
            or semantikk.MOTOR_SEMANTIKKVERSJON != r_motorver):
        # Motorsemantikken (og dermed klassifikatoren) endret seg siden runden
        # åpnet → klassifiseringen godkjennerne så er stale. Ny runde kreves.
        conn.execute("UPDATE aktiveringsrunde SET status='kansellert'"
                     " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
                     (tenant, utkast_id, r_nr))
        varsel.pensjoner_runde(conn, tenant=tenant, utkast_id=utkast_id,
                               runde=r_nr)
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
        # Innholdsinvariantene i `aktiver_policy`. Fire slag deler SQLSTATE
        # `check_violation`: VERSJONEN (migrasjon 020 — `meta.versjon` er borte,
        # alt registrert, eller ikke nyere enn den aktive), IDENTITETEN
        # (migrasjon 023 — dokumentet oppgir en annen policy enn raden),
        # STATUSEN (migrasjon 024 — dokumentet sier ikke `produksjon`) og
        # INNFØRINGSKRAVET (migrasjon 022 — en verifikator-id som gjør
        # diffstien flertydig). Kontrollene i steg 5b fanger det som var der da
        # runden ble bygget; hit kommer bare det som traff UTENOM den styrte
        # veien i vinduet etterpå — eller, for innføringskravet, en runde som
        # var ferdig attestert før utrullingen som innførte det.
        #
        # Uansett hvilket av dem: runden er død. Innholdet er frosset, så
        # verken versjonen, id-en eller statusen kan rettes uten et nytt utkast
        # og nye signaturer. Runden kanselleres derfor med det samme — en runde
        # som beviselig aldri kan aktiveres skal ikke stå åpen og se levende ut.
        # Signaturene består (append-only); det er sporet av hva som faktisk
        # ble godkjent.
        #
        # UTFALLET må derimot skilles: de krever ulik retting av eier (øk
        # versjonen vs. rett id-en vs. rett statusen), og «versjonen er i bruk»
        # om en verifikator-id er en feilmelding som sender eier feil vei.
        # Funksjonen NAVNGIR derfor bruddet (`USING CONSTRAINT`), så skillet
        # leses maskinelt og ikke ut av feilteksten.
        conn.execute("ROLLBACK TO SAVEPOINT aktiveringsforsok")
        return _kanseller_runde(
            conn, tenant, idempotency_key, utkast_id, policy_id, r_nr,
            _DOKUMENTBRUDD.get(e.diag.constraint_name, "versjon_i_bruk"))
    conn.execute("RELEASE SAVEPOINT aktiveringsforsok")

    # Runden er brukt. Ingen venter lenger på noen — heller ikke de
    # godkjennerne som aldri rakk å svare (Codex P2).
    varsel.pensjoner_runde(conn, tenant=tenant, utkast_id=utkast_id, runde=r_nr)

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
    # Godkjennerne ventet på noe som aldri kan komme; varselet skal si det
    # samme som runden gjør (Codex P2).
    varsel.pensjoner_runde(conn, tenant=tenant, utkast_id=utkast_id,
                           runde=runde)
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


def _idempotent_laas(conn, tenant: str, idempotency_key: str) -> None:
    """Advisory-låsen som serialiserer per idempotensnøkkel.

    Nøkkelformelen står ETT sted (Codex P2): to utskrifter av den er to
    låser, og to låser serialiserer ingenting. Både claimet og den
    ventende etterprøven under bruker denne.
    """
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 (f"{tenant}\x1fpolidem\x1f{idempotency_key}",))


def _idempotent_start(conn, tenant: str, idempotency_key: str,
                      input_hash: str, request_id: str):
    """Claim en idempotensnøkkel i kallerens tx (spec: `Idempotency-Key` på ALLE
    skriveruter, Codex P1 R3). -> ("ny", None) fortsett · ("replay", dict)
    returner lagret respons · ("konflikt", None) samme nøkkel, ANNET input.
    Serialiserer per nøkkel med en advisory-lås, som unntaksbehandlingen."""
    _idempotent_laas(conn, tenant, idempotency_key)
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


def idempotent_svar(conn, tenant: str, idempotency_key: str, input_hash: str,
                    vent_paa_vinner: bool = False):
    """Se på en idempotensnøkkel UTEN å claime den.
    -> ("replay", dict) · ("konflikt", None) · ("ukjent", None).

    `_idempotent_start` claimer, og claimet hører hjemme i selve
    operasjonens transaksjon. Men noen ruter må gjøre arbeid FØR den —
    rullbakkopprettelsen henter kildeversjonen for å bygge innholdet — og
    det arbeidet kan feile av grunner som ikke lenger er sanne for et
    forsøk som ALT har lyktes: kilden kan være arkivert siden. Da skal
    retryen få det lagrede svaret, ikke en 404 på et utkast som finnes
    (047, Codex P2).

    Ren lesing: ingen advisory-lås, ingen rad skrives. Er svaret ikke
    ferdig ennå, faller kalleren tilbake til den vanlige veien, og
    `_idempotent_start` avgjør som før — der ligger serialiseringen.

    `vent_paa_vinner` snur det for ETTERPRØVEN (Codex P2). En overlappende
    retry ser `ukjent` i forkontrollen fordi originalens idempotensrad
    ennå ikke er committet, og et READ COMMITTED-snapshot kan ikke se den.
    Rekker originalen å committe — og kilden å bli slettet — før retryen
    slår opp versjonen, ville et lås-løst gjensyn fortsatt kunne bomme:
    svaret er da et 404 på en nøkkel som ALT bærer et lagret 201, og en
    senere retry ville replayet det samme. Låsen holdes av vinneren HELE
    dens transaksjon, så å ta den her er å vente på at utfallet er
    avgjort. Den koster bare i feilveien; forkontrollen er urørt."""
    if vent_paa_vinner:
        _idempotent_laas(conn, tenant, idempotency_key)
    rad = conn.execute(
        "SELECT input_hash, status, respons FROM idempotens"
        " WHERE tenant=%s AND nokkel=%s",
        (tenant, idempotency_key)).fetchone()
    if rad is None:
        return ("ukjent", None)
    lagret_hash, istatus, respons = rad
    if lagret_hash != input_hash:
        return ("konflikt", None)
    if istatus == "ferdig":
        return ("replay", {**respons, "replay": True})
    return ("ukjent", None)


def _fullfor(conn, tenant, idempotency_key, res: dict) -> dict:
    """Lagre den idempotente responsen og commit. Replay med samme nøkkel og
    input får NØYAKTIG denne responsen — aldri en ny operasjon."""
    conn.execute("UPDATE idempotens SET status='ferdig', respons=%s"
                 " WHERE tenant=%s AND nokkel=%s",
                 (json.dumps(res, ensure_ascii=False), tenant, idempotency_key))
    conn.commit()
    return res
