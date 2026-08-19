"""Policyregisteret (v2 Del 1.5) — lasting med revalidering.

Ingen cache i PR-005: policyen leses fra databasen per forespørsel. Det er
et bevisst valg fra spesifikasjonen — cache-invalidering er et helt eget
problem, og på dagens skala kjøper den ingenting. Cache er M-38-scope.
"""
from __future__ import annotations

import hashlib
import json
import os

import psycopg
from miljo import er_produksjon

#: Statusene en policy kan ha i `meta.status`. I produksjon er listen
#: HARDKODET og kan ikke konfigureres bort — en policy merket `utkast` skal
#: aldri kunne binde en ekte handling fordi noen satte en miljøvariabel.
PRODUKSJONSSTATUSER = frozenset({"produksjon"})
STAGING_STANDARD = "utkast,validert_pilot,produksjon"


class PolicyUkjent(Exception):
    """Ingen aktiv policy med den id-en for DENNE tenanten."""


class PolicyKorrupt(Exception):
    """Raden finnes, men innholdet består ikke valideringen på nytt."""

    def __init__(self, feil: list[str], raa: object = None) -> None:
        super().__init__("; ".join(feil[:3]))
        self.feil = feil
        self.raa = raa


def innholds_hash(policy: dict) -> str:
    """Kanonisk SHA-256 — samme regler som attestering.kanonisk_bytes."""
    return hashlib.sha256(json.dumps(
        policy, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def tillatte_statuser() -> frozenset[str]:
    # Samme kilde og samme eksakte tolkning som kundeflaten leser miljøet med
    # (`miljo.gjeldende_miljo` i ui/server) — se `miljo`-modulen.
    if er_produksjon():
        return PRODUKSJONSSTATUSER
    raa = os.environ.get("DISPONIT_TILLATTE_POLICYSTATUSER", STAGING_STANDARD)
    return frozenset(s.strip() for s in raa.split(",") if s.strip())


def hent_aktiv(conn: psycopg.Connection, tenant: str,
               policy_id: str) -> tuple[dict, str]:
    """-> (policy, innholds_hash). Kaster PolicyUkjent / PolicyKorrupt.

    Oppslaget er ALLTID bundet til kontekstens tenant. `policy_id` fra
    forespørselen er kun et navn innenfor den tenanten — den kan aldri peke
    ut en annen tenants policy, verken via spørringen eller via RLS.
    Krever at kalleren har satt `disponit.tenant` (db.pg.sett_kontekst).

    Låsen FØRST, og delt (Codex P1). Uten den kunne `slett_ubrukt_policy`
    (032) kile seg inn i dette vinduet: beslutningen leser policyen her,
    slettingen ser en revisjonslogg uten spor av den, sletter — og så
    committer beslutningen revisjonsraden sin, som nå peker på en policy som
    ikke finnes. `FOR UPDATE` på `policy_hode` inne i slettefunksjonen stengte
    ikke det vinduet, for denne veien rører aldri `policy_hode`. Den delte
    låsen holder til kallerens transaksjon committer, altså til revisjonsraden
    STÅR — og slettingens eksklusive lås på samme nøkkel venter på nettopp det.
    Delte låser blokkerer ikke hverandre, så beslutninger går som før.
    """
    from db.pg import laas_policy_delt
    laas_policy_delt(conn, tenant, policy_id)
    rad = conn.execute(
        "SELECT innhold, innholds_hash, status, versjon FROM policyer"
        " WHERE tenant=%s AND policy_id=%s AND aktiv",
        (tenant, policy_id)).fetchone()
    if rad is None:
        raise PolicyUkjent(policy_id)
    innhold, lagret_hash, status, versjon = rad

    # Revalidering ved lasting (v2 1.5, fail-closed mot DB-korrupsjon).
    # At raden BESTO valideringen den dagen den ble skrevet, sier ingenting
    # om hva som står der nå.
    if not isinstance(innhold, dict):
        raise PolicyKorrupt(["policyinnholdet er ikke et objekt"], innhold)

    faktisk = innholds_hash(innhold)
    if faktisk != lagret_hash:
        raise PolicyKorrupt(
            [f"innholds_hash stemmer ikke: lagret {lagret_hash[:12]}…,"
             f" faktisk {faktisk[:12]}…"], innhold)

    from policy_validator.schema import valider_policy
    feil = valider_policy(innhold)
    if feil:
        raise PolicyKorrupt(feil, innhold)

    tillatt = tillatte_statuser()
    if status not in tillatt:
        raise PolicyKorrupt(
            [f"policystatus '{status}' er ikke tillatt i dette miljøet"
             f" (tillatt: {sorted(tillatt)})"], innhold)
    meta_status = (innhold.get("meta") or {}).get("status")
    if meta_status != status:
        # Kolonnen brukes til filtrering, meta.status havner i loggposten.
        # Spriker de, er det uklart hva beslutningen faktisk ble tatt under.
        raise PolicyKorrupt(
            [f"meta.status '{meta_status}' != registerets status '{status}'"],
            innhold)
    if (innhold.get("meta") or {}).get("versjon") != versjon:
        raise PolicyKorrupt(
            [f"meta.versjon != registerets versjon '{versjon}'"], innhold)
    meta_pid = (innhold.get("meta") or {}).get("policy_id")
    if meta_pid != policy_id:
        # Den tredje av de samme tre — og den som manglet (Codex P1). Status og
        # versjon ble revalidert mot registeret; identiteten var den eneste av
        # de tre dokumentet fikk oppgi fritt. Spriker den, er raden indeksert
        # under én policy mens motoren bygger beslutningens policyreferanse fra
        # dokumentets egen (`engine.policyreferanse`). Beslutningen ville da
        # blitt tatt — og logget — under en id ingen kan slå opp igjen, og
        # M-37-gjenopprettingen ville lett etter en aktiv rad som ikke finnes.
        # Fail-closed: en policy som ikke vet hvem den er, binder ingenting.
        raise PolicyKorrupt(
            [f"meta.policy_id '{meta_pid}' != registerets policy_id"
             f" '{policy_id}'"], innhold)
    return innhold, lagret_hash


def hent_aktiv_bak_loggreferanse(
        conn: psycopg.Connection, tenant: str,
        loggreferanse: object) -> tuple[dict, str] | None:
    """Den AKTIVE policyen som en revisjonsreferanse navngir, ellers None.

    `loggreferanse` er en `revisjonslogg.policy_id`-verdi — `pid@versjon/
    handling` — lest ut av en COMMITTET loggrad for SAMME tenant. Det er
    ikke en formalitet, det er hele sikkerheten i funksjonen, og derfor er
    det den eneste inngangen: de to kallerne (`m37.arbeider._aktiv_policy`
    når en reparasjon planlegges, og `api.app._ingest_verifikasjon` når
    aktiv autoritet måles før bevis godtas) leser loggraden og sender
    verdien hit uten å ha en policy-id å gi.

    HVORFOR INGEN `laas_policy_delt` HER (Codex P1). Låsen i `hent_aktiv` og
    `policyadmin._hode_aktiv_versjon` finnes for lesere som ennå IKKE har et
    spor: beslutningen har ikke skrevet revisjonsraden sin, runde-åpningen
    har ikke satt inn runden. De må holde slettingen ute til referansen
    STÅR. Her står den allerede — og den er nettopp den raden
    `slett_ubrukt_policy` (032) teller når den avgjør «aldri brukt»
    (`policy_id LIKE pid || '@%'`). En policy som er navngitt av en loggrad
    er derfor allerede uslettelig, og permanent: `revisjonslogg` er
    append-only (001, `revisjonslogg_er_append_only` avviser UPDATE, DELETE
    og TRUNCATE), så referansen kan ikke fjernes igjen.

    Låsen ville heller ikke løst noe for en leser UTEN et spor: den utsetter
    slettingen til leseren committer, men leseren etterlater ingenting
    slettingen kan se — policyen ville blitt slettet like etterpå, med
    autorisasjonen like foreldet. Vernet er referansen; låsen er bare måten
    en referanse som er UNDERVEIS rekker frem. Skulle en fremtidig kaller
    trenge den aktive policyen uten å ha en loggreferanse, er den derfor
    ikke en kaller av denne funksjonen — den må ha sitt eget vern.
    """
    from policy_validator.engine import les_policyref
    ref = les_policyref(loggreferanse)
    if ref is None:
        return None                # ingen verifiserbar policyidentitet
    rad = conn.execute(
        "SELECT innhold, innholds_hash FROM policyer"
        " WHERE tenant=%s AND policy_id=%s AND aktiv",
        (tenant, ref[0])).fetchone()
    if rad is None or not isinstance(rad[0], dict):
        return None
    return rad[0], rad[1]


def registrer(conn: psycopg.Connection, tenant: str, policy: dict,
              status: str, aktiver: bool = True) -> str:
    """Legger inn en policyversjon, eventuelt som den aktive. -> innholds_hash.

    Kun etter bestått validering (v2 1.5). Aktiveringen er atomisk:
    deaktiver forrige og aktiver ny i samme transaksjon. Delindeksen
    `en_aktiv_per_policy` er den bindende garantien — den holder selv om
    denne funksjonen skulle bli omgått.

    Brukes av token-/oppsettsveien og av testene, ikke av forespørsels-
    veien: runtime-rollen har kun SELECT på `policyer`.
    """
    from db.pg import sett_tenant
    from policy_validator.schema import valider_ny_policy
    # Setter tenanten selv, i motsetning til `hent_aktiv`. Forskjellen er
    # tilsiktet: `hent_aktiv` kjører alltid inne i forespørselsveien, der
    # `sett_kontekst` allerede har satt den, og en ekstra setting der ville
    # skjult at noen hadde glemt den. `registrer` er en frittstående
    # administrativ operasjon uten en slik eier — uten dette treffer den
    # bare row level security med FORCE og feiler på skriving.
    sett_tenant(conn, tenant)
    # INNFØRINGSKONTRAKTEN, ikke lastekontrakten: dette er en policy på vei
    # INN, og da gjelder også kravene som bare kan stilles framover (Codex P1
    # på #63). `hent_aktiv` over bruker `valider_policy` — det som allerede
    # ligger der skal fortsette å virke.
    feil = valider_ny_policy(policy)
    if feil:
        raise PolicyKorrupt(feil, policy)
    meta = policy.get("meta") or {}
    if meta.get("status") != status:
        raise PolicyKorrupt(
            [f"meta.status '{meta.get('status')}' != oppgitt status '{status}'"],
            policy)
    pid, versjon, h = meta["policy_id"], meta["versjon"], innholds_hash(policy)
    # Samme per-policy-lås som husets øvrige skrivere (Codex P2 på #73): en
    # `registrer(..., aktiver=False)` hvis INSERT ennå ikke var committet da
    # slettingens DELETE tok sitt snapshot, ville overlevd slettingen —
    # endepunktet melder suksess mens en versjon står igjen og okkuperer
    # nummeret sitt. Delt lås venter på sletterens eksklusive, og omvendt.
    from db.pg import laas_policy_delt
    laas_policy_delt(conn, tenant, pid)
    if aktiver:
        # BOOTSTRAP, ikke aktivering (047, Codex P2). Denne veien har ingen
        # runde, ingen attestasjoner og ingen hendelse — den er til for den
        # FØRSTE policyen en tenant får (`init-tenant.sh`), før det finnes
        # noe å ha fire øyne på. Etter 047 er det derfor to ting den må
        # gjøre eksplisitt:
        #
        # 1. Si hva den er. `aktivert_av_operasjon` må være NULL — det
        #    finnes ingen hendelse å peke på — men NULL alene betydde
        #    «ubundet historisk versjon», altså en rad fra før lineagen
        #    fantes. En bootstrap skrevet i dag er ikke det, og historikken
        #    kunne ikke se forskjell. `aktiveringskilde='bootstrap'` sier
        #    det raden faktisk er.
        #
        # 2. Aldri gå FORBI en styrt aktivering. Har policyen ENGANG vært
        #    aktivert gjennom fire-øyne-veien, er serien inne i lineagen,
        #    og en oppsettskjøring som setter inn sin egen hendelsesløse
        #    rad ville tatt den ut igjen uten at noe sa fra — nøyaktig den
        #    omgåingen 047 er til for å hindre. Da er svaret at aktivering
        #    er en styrt handling, ikke en registrering.
        #
        # PRØVEN MÅLER HENDELSEN, IKKE DEN AKTIVE RADEN (Codex P1). En
        # tidligere utgave spurte `policyer` om den NÅVÆRENDE aktive raden
        # bar `aktivert_av_operasjon`. Det er en tilstand som kan forsvinne:
        # `slett_ubrukt_policy` (032) sletter en ubrukt versjon — også en
        # styrt aktivert en — mens `policyaktivering` er immutabel og blir
        # stående. Etter en slik sletting fant prøven ingen aktiv styrt rad,
        # og oppsettsveien åpnet seg igjen for en serie som for lengst har
        # gått inn i lineagen: en oppsettskjøring kunne gjenskape samme
        # policy/versjon som bootstrap, historikken ville vist den som
        # bootstrap, og den forrige aktiveringshendelsen ville ligget
        # frakoblet ved siden av. Døren stenges av det som ikke kan slettes.
        #
        # ANKERRADEN LÅSES FØRST (Codex P1). Den delte advisory-låsen over
        # serialiserer denne veien mot SLETTINGEN, som tar den eksklusive
        # varianten — men ikke mot `aktiver_policy`, som ikke tar den i det
        # hele tatt. Autoriteten den styrte veien serialiserer på er
        # `policy_hode`-raden (steg 4 i 022/047: `SELECT aktiv_versjon ...
        # FOR UPDATE`), og uten den her kunne en aktivering committe mellom
        # prøven under og `UPDATE policyer SET aktiv=false` rett etter.
        # Bootstrapen ville da deaktivert en nettopp styrt aktivert versjon
        # og satt inn sin egen hendelsesløse rad — nøyaktig omgåingen
        # prøven finnes for, bare gjennom et vindu i stedet for en dør.
        #
        # Radlåsen er tilgjengelig HER, i motsetning til i
        # forespørselsveien `laas_policy_delt` er skrevet for: `registrer`
        # er en administrativ operasjon som uansett skriver `policy_hode`
        # lenger nede, altså har UPDATE på tabellen. Ankerraden opprettes
        # om den mangler, med samme `ON CONFLICT DO NOTHING` som
        # aktiveringen bruker — låsen skal tas på en rad som finnes.
        conn.execute(
            "INSERT INTO policy_hode (tenant, policy_id) VALUES (%s,%s)"
            " ON CONFLICT (tenant, policy_id) DO NOTHING", (tenant, pid))
        conn.execute(
            "SELECT aktiv_versjon FROM policy_hode WHERE tenant=%s"
            " AND policy_id=%s FOR UPDATE", (tenant, pid))
        # Egen setning ETTER låsen: under READ COMMITTED tar den et ferskt
        # snapshot, så en aktivering som committet mens vi ventet på låsen
        # ER synlig her. `policyaktivering` leses direkte: `registrer` er
        # oppsettsveien og kjører på migratorforbindelsen (se docstringen),
        # og tenantporten står — `sett_tenant` øverst satte GUC-en RLS-
        # policyen på tabellen måler mot.
        styrt = conn.execute(
            "SELECT versjon, decision_operation_id FROM policyaktivering"
            " WHERE tenant=%s AND policy_id=%s"
            " ORDER BY aktivert_ts DESC, decision_operation_id DESC LIMIT 1",
            (tenant, pid)).fetchone()
        if styrt is not None:
            raise PolicyKorrupt(
                [f"kan ikke registrere versjon {versjon} som aktiv:"
                 f" {pid} er aktivert gjennom fire-øyne-veien"
                 f" ({pid}@{styrt[0]}, operasjon {styrt[1]}). En ny versjon"
                 " må aktiveres samme vei (policyadmin), ikke gjennom"
                 " oppsettsregistreringen"], policy)
        # MÅLVERSJONEN HOLDES UTENFOR (Codex P2). «Deaktiver den forrige»
        # gjelder de ANDRE versjonene; å slå av flagget på den raden vi er i
        # ferd med å slå det på igjen er ingen overgang. Det var heller ikke
        # gratis: upserten under avgjør aktiveringstidspunktet ved å se på
        # om raden ALT var aktiv, og en re-registrering av den aktive
        # versjonen hadde da alltid nullstilt den prøven selv.
        conn.execute("UPDATE policyer SET aktiv=false"
                     " WHERE tenant=%s AND policy_id=%s AND aktiv"
                     " AND versjon<>%s", (tenant, pid, versjon))
    else:
        # `aktiver=False` på DEN VERSJONEN SOM ER AKTIV er ikke en
        # registrering — det er en avvikling, og den skal ikke skje som
        # bivirkning av en re-registrering.
        #
        # Upserten under setter `aktiv = EXCLUDED.aktiv`. Kalles funksjonen
        # med samme versjon og `aktiver=False`, slås altså flagget av på den
        # gjeldende raden mens pekeren blir stående. Det er speilbildet av
        # rotårsaken denne fiksen handler om — og verre: `hent_aktiv` finner
        # da INGEN aktiv rad og kaster `PolicyUkjent` på hver beslutning,
        # mens styringslaget fortsatt tror en policy er i kraft. Tenanten
        # mister policyen sin uten at noe sier fra.
        #
        # Å avvikle en policy i kraft er en styringshandling. Den skal ha sin
        # egen, sporede vei — ikke denne.
        rad = conn.execute(
            "SELECT 1 FROM policyer WHERE tenant=%s AND policy_id=%s"
            " AND versjon=%s AND aktiv", (tenant, pid, versjon)).fetchone()
        peker = conn.execute(
            "SELECT 1 FROM policy_hode WHERE tenant=%s AND policy_id=%s"
            " AND aktiv_versjon=%s", (tenant, pid, versjon)).fetchone()
        if rad or peker:
            raise PolicyKorrupt(
                [f"kan ikke registrere versjon {versjon} med aktiver=False:"
                 " den er den gjeldende aktive versjonen — avvikling er en"
                 " egen, styrt handling"], policy)
    # En versjon som ER en styrt aktivering kan ikke skrives om herfra —
    # heller ikke som inaktiv historikk. `innholds_hash` inngår i FK-en mot
    # hendelsen, og attestantene signerte NØYAKTIG det innholdet; en upsert
    # som byttet det ville enten brutt FK-en ved commit (med en feilmelding
    # ingen kan lese) eller flyttet attestasjonen over på et annet dokument.
    bundet = conn.execute(
        "SELECT aktivert_av_operasjon FROM policyer WHERE tenant=%s"
        " AND policy_id=%s AND versjon=%s AND aktivert_av_operasjon IS NOT"
        " NULL", (tenant, pid, versjon)).fetchone()
    if bundet is not None:
        raise PolicyKorrupt(
            [f"kan ikke registrere versjon {versjon} på nytt: den ble"
             f" aktivert gjennom fire-øyne-veien (operasjon {bundet[0]}),"
             " og innholdet er bundet til attestasjonene"], policy)
    # `aktiveringskilde='bootstrap'` merker VEIEN INN, ikke bare
    # aktiveringen: raden kom gjennom oppsettsregistreringen, med eller uten
    # flagget. Uten merket var den ikke til å skille fra en rad som lå der
    # da 047 landet (047, Codex P2).
    #
    # AKTIVERINGSTIDSPUNKTET skrives her (047, Codex P2). Bootstrapen har
    # ingen hendelse å hente det fra, og `opprettet` er ikke svaret: den
    # står for REGISTRERINGEN, og upserten under aktiverer i mange tilfeller
    # en rad som ble lagt inn med `aktiver=False` for lenge siden. Uten et
    # eget tidspunkt sorterte historikken den nyaktiverte versjonen på et
    # gammelt merke. `now()` er transaksjonens starttid, samme klokke som
    # `policyaktivering.aktivert_ts` bruker.
    #
    # `aktiver=False` LAR merket stå: en avaktivering fjerner ikke at raden
    # en gang ble aktivert, og denne veien kan uansett ikke avvikle den
    # gjeldende aktive versjonen (porten over).
    #
    # MERKET SETTES BARE VED EN FAKTISK OVERGANG (Codex P2). Funksjonen er
    # med vilje en upsert: en administrativ re-kjøring av samme
    # registrering skal være ufarlig. Skrev vi `now()` på hver
    # `aktiver=True`, var den ikke det — en re-registrering av den ALT
    # aktive versjonen ga den et ferskt aktiveringstidspunkt, uten at noen
    # versjonsovergang hadde skjedd. Historikken sorterer på nettopp dette
    # merket, så raden hoppet til topps og snudde diffens default-retning,
    # og «sist aktivert» sa når noen sist kjørte oppsettet i stedet for når
    # policyen faktisk ble tatt i bruk. `policyer.aktiv` er tilstanden FØR
    # upserten (og målversjonen ble holdt utenfor deaktiveringen over,
    # nettopp så den prøven er sann), altså: bare en rad som IKKE var aktiv
    # blir «nå aktivert».
    conn.execute(
        "INSERT INTO policyer (tenant, policy_id, versjon, innholds_hash,"
        " status, innhold, aktiv, aktiveringskilde, bootstrap_aktivert_ts)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,'bootstrap',"
        "         CASE WHEN %s THEN now() END)"
        " ON CONFLICT (tenant, policy_id, versjon) DO UPDATE"
        " SET innholds_hash=EXCLUDED.innholds_hash, status=EXCLUDED.status,"
        "     innhold=EXCLUDED.innhold, aktiv=EXCLUDED.aktiv,"
        "     aktiveringskilde=EXCLUDED.aktiveringskilde,"
        "     bootstrap_aktivert_ts=CASE"
        "         WHEN EXCLUDED.aktiv AND NOT policyer.aktiv THEN now()"
        "         ELSE policyer.bootstrap_aktivert_ts END",
        (tenant, pid, versjon, h, status, json.dumps(policy), aktiver,
         aktiver))
    if aktiver:
        # Ankerraden MÅ følge med. Den styrte aktiveringen
        # (`aktiver_policy`) leser `policy_hode.aktiv_versjon`, IKKE
        # `policyer.aktiv`. Skrev vi bare flagget — som denne funksjonen
        # gjorde — trodde den at ingenting var aktivt, hoppet derfor over
        # «deaktiver forrige», og INSERT-en kolliderte med delindeksen
        # `en_aktiv_per_policy`. Symptomet var HTTP 500 midt i en
        # fire-øyne-runde, på en tenant satt opp helt normalt via
        # init-tenant.sh, og det rammet FØRSTE styrte aktivering for enhver
        # slik tenant. Delindeksen holdt — men den holdt ved å velte
        # forespørselen, ikke ved å hindre at pekeren kom ut av synk.
        conn.execute(
            "INSERT INTO policy_hode (tenant, policy_id, aktiv_versjon)"
            " VALUES (%s,%s,%s)"
            " ON CONFLICT (tenant, policy_id) DO UPDATE"
            " SET aktiv_versjon = EXCLUDED.aktiv_versjon,"
            "     revisjon = policy_hode.revisjon + 1",
            (tenant, pid, versjon))
    return h
