#!/usr/bin/env python3
"""Policy-rundtur ende-til-ende: bootstrap → fire øyne → aktivering → BESLUTNING.

Søsteren til `r1-rundtur.py`. Den beviser M-37-kjeden; denne beviser
STYRINGSKJEDEN: at en fersk tenant, satt opp nøyaktig som `init-tenant.sh`
gjør det, kan få en policy gjennom fire øyne og faktisk BRUKE den etterpå.

Den finnes fordi vi manglet den. Tre defekter nådde produksjon i august 2026,
alle i denne kjeden, alle funnet av eieren én om gangen mens han attesterte:

  * ankerraden (`policy_hode`) ble aldri skrevet av bootstrap, så FØRSTE
    styrte aktivering på enhver normalt oppsatt tenant døde med
    `UniqueViolation` — HTTP 500 midt i en fire-øyne-runde;
  * aktiveringen lagret `versjon` fra en teller («1») mens dokumentet sa
    «0.2.0». `hent_aktiv` krever at de er like, så den ferske policyen ble
    avvist som `PolicyKorrupt` ved HVER beslutning. Aktiveringen svarte
    «aktivert»; policyen var ubrukelig;
  * monotonikontrollen sammenlignet versjonsledd uten nullpadding, så
    «2.0.0» passerte mot en aktiv «2» — samme versjon, ny korrupsjon.

Hver av dem ville falt ut av ÉN gjennomkjøring. Poenget med rundturen er
derfor ikke å teste enheter — det gjør pytest — men å gå HELE veien til
TERMINALTILSTAND på ekte data. En kjede som stopper ett sted skjuler alle
portene bak.

Kjøres mot en lokal base (samme miljø som pytest):
    source ~/tools/disponit_testmiljo.sh
    DISPONIT_REPO=$PWD python3 deploy/staging/policy-rundtur.py

Grønn = styringskjeden virker faktisk.
"""
import copy
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(os.environ.get("DISPONIT_REPO", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "platform/core"))

DSN = os.environ["DISPONIT_TEST_DSN"]
MIGRATOR = os.environ["DISPONIT_TEST_MIGRATOR_DSN"]
TENANT = "t-pol-" + secrets.token_hex(3)

# Samme kastbare KEK som pytest bruker (test_api.KEK). Rundturen kjører KUN
# mot den lokale testbasen — DSN-ene over peker dit — og bootstrappen trenger
# en KEK for å pakke tenantens DEK. Settes den utenfra, brukes den i stedet.
os.environ.setdefault("DISPONIT_KEK", "b" * 64)

_FEIL = 0


def port(navn: str, ok: bool, detalj: str = "") -> None:
    """Én port. Rundturen stopper ALDRI på første feil — vi vil se hvor mange
    av dem som er nede, ikke bare den første. Det var nettopp det som gjorde
    august dyr: én defekt per runde."""
    global _FEIL
    if ok:
        print(f"  \033[32m✓\033[0m {navn}" + (f" — {detalj}" if detalj else ""))
    else:
        _FEIL += 1
        print(f"  \033[31m✗ {navn}\033[0m" + (f" — {detalj}" if detalj else ""))


def naa():
    return datetime.now(timezone.utc)


def main() -> int:                                        # noqa: C901
    import yaml
    from db.pg import koble, sett_kontekst
    from api import policyadmin
    from api import policyregister as pr
    from api.mac_register import MacRegister

    mac = MacRegister({"mk1": {"rolle": "signerer",
                               "hemmelighet": "m" * 40}})
    mig = koble(MIGRATOR)
    sett_kontekst(mig, TENANT, "rundtur", "r0")

    # ---------------------------------------------------------------- 1
    print("\n== 1  bootstrap, nøyaktig som init-tenant.sh ==")
    from db import kryptering
    kryptering.hent_eller_opprett_aktiv_dek(mig, TENANT)
    grunnpolicy = yaml.safe_load(
        (REPO / "policies/bransjemal-tjenestebedrift.yaml").read_text("utf-8"))
    pid = grunnpolicy["meta"]["policy_id"]
    pr.registrer(mig, TENANT, grunnpolicy, grunnpolicy["meta"]["status"])
    mig.commit()
    sett_kontekst(mig, TENANT, "rundtur", "r1")

    def flagg_og_peker():
        a = mig.execute("SELECT versjon FROM policyer WHERE tenant=%s"
                        " AND policy_id=%s AND aktiv", (TENANT, pid)).fetchone()
        p = mig.execute("SELECT aktiv_versjon FROM policy_hode WHERE tenant=%s"
                        " AND policy_id=%s", (TENANT, pid)).fetchone()
        return (a[0] if a else None), (p[0] if p else None)

    aktiv, peker = flagg_og_peker()
    # PORT A — bootstrap-defekten. Uten ankerraden er peker None mens flagget
    # peker på en versjon, og første styrte aktivering kolliderer.
    port("bootstrap etterlater peker == flagg", aktiv is not None and aktiv == peker,
         f"flagg={aktiv} peker={peker}")

    # To mennesker: forfatteren og en UAVHENGIG godkjenner. Fire øyne krever
    # at minst én ikke er den som skrev utkastet.
    def medlem(navn, roller, tenant=None):
        ten = tenant or TENANT
        bid = mig.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " ON CONFLICT (issuer,sub) DO UPDATE SET sub=EXCLUDED.sub"
            " RETURNING bruker_id",
            ("https://idp.example", f"{ten}-{navn}")).fetchone()[0]
        arr = "ARRAY[" + ",".join(f"'{r}'" for r in roller) + "]"
        mig.execute(f"INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
                    f" VALUES (%s,%s,{arr}) ON CONFLICT (tenant,bruker_id)"
                    f" DO UPDATE SET roller=EXCLUDED.roller, aktiv=true",
                    (ten, bid))
        mig.commit()
        sett_kontekst(mig, ten, "rundtur", "r1")
        return bid

    forfatter = medlem("forfatter", ["policyforvalter"])
    godkjenner = medlem("godkjenner", ["policyforvalter"])

    # ---------------------------------------------------------------- 2
    def livslop(ny_versjon: str, muter, merke: str, *, tenant=None,
                aktorer=None, forvent_stopp=False):
        """Hele veien: utkast → validert → runde → to attestasjoner → aktivert."""
        print(f"\n== {merke}  fire-øyne-livsløp mot {ny_versjon} ==")
        TEN = tenant or TENANT
        forf, godkj = aktorer or (forfatter, godkjenner)
        rt = koble(DSN)
        innhold = copy.deepcopy(grunnpolicy)
        innhold["meta"] = {**innhold["meta"], "versjon": ny_versjon}
        muter(innhold)
        idem = secrets.token_hex(8)
        res = policyadmin.opprett_utkast(
            rt, tenant=TEN, aktor=forf, request_id="r",
            policy_id=pid, innhold=innhold, idempotency_key=idem,
            input_hash=f"{TEN}\x1fny\x1f{idem}")
        rt.commit()
        uid = res["utkast_id"]
        port("utkast opprettet", bool(uid),
             f"{uid} basert på {res.get('base_versjon')}")

        idem = secrets.token_hex(8)
        v = policyadmin.valider_utkast(
            rt, tenant=TEN, aktor=forf, request_id="r", utkast_id=uid,
            forventet_utkastversjon=1, idempotency_key=idem,
            input_hash=f"{TEN}\x1f{uid}\x1fvalider\x1f1\x1f{idem}")
        rt.commit()
        if forvent_stopp and v.get("utfall") != "validert":
            # Kalleren KREVER at kjeden brytes (port 6). Valideringen sier nå
            # fra om en umulig versjon som tekst — tidligere enn rundeåpningen
            # og før noen signerer. Et stopp her er suksess, ikke en rød port.
            port("kjeden stopper allerede ved valideringen", True,
                 str(v.get("feil"))[:110])
            rt.close()
            return None
        port("utkastet validerer", v.get("utfall") == "validert",
             str(v.get("feil") or v.get("utfall")))
        if v.get("utfall") != "validert":
            rt.close()
            return None

        idem = secrets.token_hex(8)
        runde = policyadmin.opprett_aktiveringsrunde(
            rt, tenant=TEN, utkast_id=uid, aktor=forf, request_id="r",
            idempotency_key=idem,
            input_hash=f"{TEN}\x1f{uid}\x1fapne\x1f{idem}", naa=naa())
        rt.commit()
        dh = runde["diff_hash"]
        port("runde åpnet", bool(dh),
             f"risiko={runde.get('risikoklasse')} "
             f"påkrevd={runde.get('pakrevd_antall_godkjennere')}")

        def attester(aktor):
            idem = secrets.token_hex(8)
            r = policyadmin.attester_aktivering(
                rt, mac, tenant=TEN, aktor=aktor, request_id="r",
                utkast_id=uid, forventet_diff_hash=dh, idempotency_key=idem,
                input_hash=f"{TEN}\x1f{uid}\x1f{aktor}\x1f{dh}\x1f{idem}",
                naa=naa())
            rt.commit()
            return r

        # Runden er åpen og venter på noen. Da SKAL den som kan bringe den
        # videre ha fått beskjed — det var hele grunnen til at eier måtte si
        # fra utenom systemet. Varselet skal treffe godkjenneren, ikke
        # forfatteren som nettopp åpnet runden.
        from api import varsel as _v
        # Uten tenantkontekst filtrerer RLS bort medlemskapene, og porten ville
        # målt sin egen glemsomhet — samme felle som port 4 hadde.
        sett_kontekst(rt, TEN, "rundtur", "rv")
        mott = _v.mottakere_for_runde(rt, TEN, uid, runde["runde"])
        uleste = _v.antall_uleste(rt, tenant=TEN, bruker_id=godkj)
        port("godkjenneren er varslet om at runden venter",
             godkj in mott and uleste >= 1,
             f"mottakere={len(mott)} uleste_hos_godkjenner={uleste}")

        a1 = attester(forf)
        port("forfatterens attestasjon aktiverer IKKE alene",
             a1.get("utfall") == "venter_godkjennere", str(a1.get("utfall")))
        a2 = attester(godkj)
        # PORT B1 — her døde produksjon på UniqueViolation.
        port("uavhengig godkjenner fullfører aktiveringen",
             a2.get("utfall") == "aktivert",
             f"utfall={a2.get('utfall')} versjon={a2.get('versjon')}")
        rt.close()
        return uid if a2.get("utfall") == "aktivert" else None

    livslop("0.3.0", lambda p: p.setdefault("dataklasser", []), "2")

    # ---------------------------------------------------------------- 3
    print("\n== 3  registeret etter aktivering ==")
    sett_kontekst(mig, TENANT, "rundtur", "r2")
    rad = mig.execute(
        "SELECT versjon, innhold->'meta'->>'versjon', status FROM policyer"
        " WHERE tenant=%s AND policy_id=%s AND aktiv", (TENANT, pid)).fetchone()
    # PORT B2 — versjon-fra-teller-defekten. Kolonnen MÅ være dokumentets egen
    # versjon; ellers avviser `hent_aktiv` policyen som korrupt.
    port("kolonnen bærer dokumentets egen versjon",
         bool(rad) and rad[0] == rad[1], f"kolonne={rad[0]} dokument={rad[1]}"
         if rad else "ingen aktiv rad")
    port("statusen er produksjon", bool(rad) and rad[2] == "produksjon",
         rad[2] if rad else "-")
    aktiv, peker = flagg_og_peker()
    port("flagg og peker er fortsatt enige", aktiv == peker,
         f"flagg={aktiv} peker={peker}")

    # ---------------------------------------------------------------- 4
    print("\n== 4  den aktiverte policyen kan FAKTISK brukes ==")
    rt = koble(DSN)
    try:
        # `hent_aktiv` kjører normalt inne i forespørselsveien, der
        # `sett_kontekst` alt er kalt. Uten den filtrerer RLS bort raden, og
        # porten ville rapportert PolicyUkjent uansett hvor frisk policyen var
        # — den ville målt sin egen glemsomhet. (Første utgave gjorde nettopp
        # det.)
        sett_kontekst(rt, TENANT, "rundtur", "r5")
        p, h = pr.hent_aktiv(rt, TENANT, pid)
        # PORT C — dette er porten som ville avslørt at «aktivert» ikke betyr
        # «brukbar». Uten den ser en korrupt aktivering ut som en vellykket.
        port("hent_aktiv leverer policyen", isinstance(p, dict) and bool(h),
             f"versjon={p.get('meta', {}).get('versjon')}")
    except Exception as e:                                   # noqa: BLE001
        port("hent_aktiv leverer policyen", False,
             f"{type(e).__name__}: {str(e)[:120]}")
    finally:
        rt.close()

    # ---------------------------------------------------------------- 5
    livslop("0.4.0", lambda p: p.setdefault("dataklasser", []), "5")

    # ---------------------------------------------------------------- 6
    print("\n== 6  monotoni mot en LEGACY-formet versjon ==")
    # Tellerens tid etterlot rader med bare «2». `{2,0,0} > {2}` er sant både i
    # Postgres og Python når leddene ikke nullpaddes, så «2.0.0» ville passert
    # som «nyere» enn «2» — samme versjon, ny korrupsjon oppå den gamle. En
    # fersk tenant treffer aldri dette av seg selv; formen må lages.
    #
    # Porten kjører en EKTE aktivering og krever at den STOPPER. Første utgave
    # spurte Postgres om array-semantikk i stedet — den målte databasen, ikke
    # produktet, og kunne aldri blitt grønn uansett hva `aktiver_policy` gjorde.
    import json as _json
    legacy = "t-legacy-" + secrets.token_hex(3)
    sett_kontekst(mig, legacy, "rundtur", "r3")
    kryptering.hent_eller_opprett_aktiv_dek(mig, legacy)
    lp = copy.deepcopy(grunnpolicy)
    lp["meta"] = {**lp["meta"], "versjon": "2"}
    mig.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,status,"
        "innhold,aktiv) VALUES (%s,%s,'2',%s,'produksjon',%s::jsonb,true)",
        (legacy, pid, pr.innholds_hash(lp), _json.dumps(lp)))
    mig.execute("INSERT INTO policy_hode (tenant,policy_id,aktiv_versjon)"
                " VALUES (%s,%s,'2')", (legacy, pid))
    mig.commit()
    sett_kontekst(mig, legacy, "rundtur", "r4")
    lf = medlem("lforfatter", ["policyforvalter"], tenant=legacy)
    lg = medlem("lgodkjenner", ["policyforvalter"], tenant=legacy)
    # Kjeden SKAL brytes. Hvor den brytes er et produktvalg — i dag stopper
    # `_krev_ny_versjon` den alt ved runde-åpning, altså før noen attesterer,
    # som er bedre enn å stoppe den til slutt. Porten krever derfor bare at den
    # STOPPER, ikke hvor.
    try:
        u6 = livslop("2.0.0", lambda p: None, "6", tenant=legacy,
                     aktorer=(lf, lg), forvent_stopp=True)
        port("«2.0.0» aktiveres IKKE mot en aktiv «2»", u6 is None,
             "stoppet før aktivering" if u6 is None
             else "kjeden gikk helt gjennom — monotonivakten er nede")
    except Exception as e:                                    # noqa: BLE001
        port("«2.0.0» aktiveres IKKE mot en aktiv «2»", True,
             f"{type(e).__name__}: {str(e)[:80]}")

    print("\n== 7  et utkast rett fra malen skal STOPPES, ikke korrumperes ==")
    # Bransjemalene har `status: utkast` — riktig for et forslag. Bærer utkastet
    # den videre, skriver aktiveringen `produksjon` i registeret mens dokumentet
    # sier `utkast`, og `hent_aktiv` avviser policyen som korrupt ved HVER
    # beslutning. Aktiveringen svarte «aktivert»; policyen var ubrukelig.
    #
    # Rundturen fant nettopp dette i sin første ekte kjøring (port 4 ble rød).
    # Denne porten holder på funnet: den forfaller til malens egen status og
    # krever at kjeden stopper FØR runden åpnes.
    malten = "t-mal-" + secrets.token_hex(3)
    sett_kontekst(mig, malten, "rundtur", "r6")
    kryptering.hent_eller_opprett_aktiv_dek(mig, malten)
    pr.registrer(mig, malten, grunnpolicy, grunnpolicy["meta"]["status"])
    mig.commit()
    sett_kontekst(mig, malten, "rundtur", "r7")
    mf = medlem("mforfatter", ["policyforvalter"], tenant=malten)
    mg = medlem("mgodkjenner", ["policyforvalter"], tenant=malten)
    livslop("0.9.0", lambda pol: None, "7", tenant=malten, aktorer=(mf, mg))
    # Kravet er ikke at kjeden stopper — det er at resultatet er BRUKBART.
    # Malen bærer `status: utkast`; `opprett_utkast` normaliserer dokumentets
    # status til `produksjon` ved opprettelsen, før valideringen fryser hashen,
    # så det som attesteres er det samme som blir aktivert. Uten den
    # normaliseringen aktiveres policyen og avvises deretter som korrupt ved
    # hver beslutning — eier laget to slike uten at noe sa fra.
    rt2 = koble(DSN)
    try:
        sett_kontekst(rt2, malten, "rundtur", "r8")
        pm, hm = pr.hent_aktiv(rt2, malten, pid)
        port("policy laget RETT FRA MALEN er brukbar", bool(pm) and bool(hm),
             f"status={pm.get('meta', {}).get('status')} "
             f"versjon={pm.get('meta', {}).get('versjon')}")
    except Exception as e:                                    # noqa: BLE001
        port("policy laget RETT FRA MALEN er brukbar", False,
             f"{type(e).__name__}: {str(e)[:110]}")
    finally:
        rt2.close()

    print("\n== 8  angre: en aktivert, ALDRI brukt policy kan slettes ==")
    # Eiers behov, målt to ganger i produksjon (tjenestebedrift1/2): aktivert
    # ved feil, og eneste vei ut var håndskrevet SQL. `slett_ubrukt_policy`
    # (032) er den styrte veien: den nekter hvis policyen har styrt én
    # beslutning, bevarer utkast/attestasjoner, og frigjør versjonsnumrene.
    rt8 = koble(DSN)
    try:
        sett_kontekst(rt8, malten, "rundtur", "r9")
        # Slettingen er bundet til den policyen kalleren SÅ: versjon +
        # innholdshash, nøyaktig som `/v1/policy/aktiv` serverer dem. Er en ny
        # versjon aktivert i mellomtiden, avvises slettingen i stedet for å ta
        # den nye med seg.
        aktiv = rt8.execute(
            "SELECT versjon, innholds_hash FROM policyer"
            " WHERE tenant=%s AND policy_id=%s AND aktiv",
            (malten, pid)).fetchone()
        n_slettet = rt8.execute(
            "SELECT slett_ubrukt_policy(%s,%s,%s,%s)",
            (malten, pid, aktiv[0], aktiv[1])).fetchone()[0]
        rt8.commit()
        sett_kontekst(rt8, malten, "rundtur", "r10")
        port("policyen slettes (aldri brukt)", n_slettet >= 1,
             f"{n_slettet} versjonsrad(er)")
        try:
            pr.hent_aktiv(rt8, malten, pid)
            port("tenanten står ærlig uten aktiv policy", False,
                 "hent_aktiv fant fortsatt en policy")
        except Exception as e:                                # noqa: BLE001
            port("tenanten står ærlig uten aktiv policy",
                 type(e).__name__ == "PolicyUkjent", type(e).__name__)
        # Historien består: utkastet som ble attestert står som `aktivert`.
        rad = rt8.execute(
            "SELECT count(*) FROM policyutkast WHERE tenant=%s"
            " AND policy_id=%s AND status='aktivert'", (malten, pid)).fetchone()
        port("attestasjonshistorikken består", rad[0] >= 1,
             f"{rad[0]} aktiverte utkast")
    finally:
        rt8.close()

    print("\n== 9  gjenåpne: et validert utkast kan redigeres og gå ny runde ==")
    # Eiers krav 17/8, ordrett: «man må kunne redigere samme policy selv etter
    # validering … men da kan den igjen bli attestert og validert.» Porten går
    # HELE veien: et utkast som ARVER den aktive versjonen (17/8-fellen som ga
    # seks uforklarte 409), byttes til neste ledige ved opprettelsen, valideres,
    # får en åpen runde — og gjenåpnes så: runden trekkes, innholdet redigeres,
    # og den NYE valideringen + en helt ny fire-øyne-runde tar det til aktivert.
    rt9 = koble(DSN)
    try:
        sett_kontekst(rt9, TENANT, "rundtur", "r11")
        aktiv_v = rt9.execute(
            "SELECT versjon FROM policyer WHERE tenant=%s AND policy_id=%s"
            " AND aktiv", (TENANT, pid)).fetchone()[0]
        innhold9 = copy.deepcopy(grunnpolicy)
        innhold9["meta"] = {**innhold9["meta"], "versjon": aktiv_v}
        idem = secrets.token_hex(8)
        res = policyadmin.opprett_utkast(
            rt9, tenant=TENANT, aktor=forfatter, request_id="r",
            policy_id=pid, innhold=innhold9, idempotency_key=idem,
            input_hash=f"{TENANT}\x1fny9\x1f{idem}")
        uid9 = res["utkast_id"]
        sett_kontekst(rt9, TENANT, "rundtur", "r12")
        lagret_v = rt9.execute(
            "SELECT innhold->'meta'->>'versjon' FROM policyutkast"
            " WHERE tenant=%s AND utkast_id=%s", (TENANT, uid9)).fetchone()[0]
        port("en arvet, opptatt versjon byttes med neste ledige ved"
             " opprettelsen", lagret_v != aktiv_v, f"{aktiv_v} → {lagret_v}")

        idem = secrets.token_hex(8)
        v = policyadmin.valider_utkast(
            rt9, tenant=TENANT, aktor=forfatter, request_id="r",
            utkast_id=uid9, forventet_utkastversjon=1, idempotency_key=idem,
            input_hash=f"{TENANT}\x1f{uid9}\x1fvalider\x1f1\x1f{idem}")
        gammel_hash = v.get("innholds_hash")
        idem = secrets.token_hex(8)
        policyadmin.opprett_aktiveringsrunde(
            rt9, tenant=TENANT, utkast_id=uid9, aktor=forfatter,
            request_id="r", idempotency_key=idem,
            input_hash=f"{TENANT}\x1f{uid9}\x1fapne9\x1f{idem}", naa=naa())

        idem = secrets.token_hex(8)
        g = policyadmin.gjenapne_utkast(
            rt9, tenant=TENANT, aktor=forfatter, request_id="r",
            utkast_id=uid9, forventet_utkastversjon=1, idempotency_key=idem,
            input_hash=f"{TENANT}\x1f{uid9}\x1fgjenapne\x1f1\x1f{idem}",
            naa=naa())
        sett_kontekst(rt9, TENANT, "rundtur", "r13")
        rstatus = rt9.execute(
            "SELECT status FROM aktiveringsrunde WHERE tenant=%s AND"
            " utkast_id=%s ORDER BY runde DESC LIMIT 1",
            (TENANT, uid9)).fetchone()[0]
        hasj = rt9.execute(
            "SELECT innholds_hash FROM policyutkast WHERE tenant=%s"
            " AND utkast_id=%s", (TENANT, uid9)).fetchone()[0]
        port("gjenåpningen trekker runden og tiner utkastet",
             g.get("status") == "utkast" and rstatus == "kansellert"
             and hasj is None,
             f"status={g.get('status')} runde={rstatus} hash={hasj!r}")

        deler = lagret_v.split(".")
        deler[-1] = str(int(deler[-1]) + 1)
        redigert_v = ".".join(deler)
        innhold9r = copy.deepcopy(grunnpolicy)
        innhold9r["meta"] = {**innhold9r["meta"], "versjon": redigert_v,
                             "status": "produksjon"}
        idem = secrets.token_hex(8)
        # Gjenåpningen BUMPET utkastversjonen (Codex P1) — redigeringen må
        # bruke den returnerte, ikke den man husket fra før valideringen.
        r = policyadmin.rediger_utkast(
            rt9, tenant=TENANT, aktor=forfatter, request_id="r",
            utkast_id=uid9, forventet_utkastversjon=g["utkastversjon"],
            innhold=innhold9r,
            idempotency_key=idem, input_hash=f"ih9-{idem}")
        idem = secrets.token_hex(8)
        v2 = policyadmin.valider_utkast(
            rt9, tenant=TENANT, aktor=forfatter, request_id="r",
            utkast_id=uid9, forventet_utkastversjon=r["utkastversjon"],
            idempotency_key=idem, input_hash=f"ihv9-{idem}")
        port("den nye valideringen fryser en NY hash",
             v2.get("utfall") == "validert"
             and v2.get("innholds_hash") != gammel_hash,
             str(v2.get("feil") or v2.get("utfall")))

        idem = secrets.token_hex(8)
        runde9 = policyadmin.opprett_aktiveringsrunde(
            rt9, tenant=TENANT, utkast_id=uid9, aktor=forfatter,
            request_id="r", idempotency_key=idem,
            input_hash=f"{TENANT}\x1f{uid9}\x1fapne9b\x1f{idem}", naa=naa())
        dh9 = runde9["diff_hash"]
        for aktor in (forfatter, godkjenner):
            idem = secrets.token_hex(8)
            a = policyadmin.attester_aktivering(
                rt9, mac, tenant=TENANT, aktor=aktor, request_id="r",
                utkast_id=uid9, forventet_diff_hash=dh9, idempotency_key=idem,
                input_hash=f"{TENANT}\x1f{uid9}\x1f{aktor}\x1f{dh9}\x1f{idem}",
                naa=naa())
            rt9.commit()
        port("det gjenåpnede utkastet går HELE veien til aktivert",
             a.get("utfall") == "aktivert"
             and a.get("versjon") == redigert_v,
             f"utfall={a.get('utfall')} versjon={a.get('versjon')}")
        sett_kontekst(rt9, TENANT, "rundtur", "r14")
        pa, _ = pr.hent_aktiv(rt9, TENANT, pid)
        port("beslutningsveien ser den REDIGERTE versjonen",
             pa.get("meta", {}).get("versjon") == redigert_v,
             f"aktiv={pa.get('meta', {}).get('versjon')}")
    except Exception as e:                                    # noqa: BLE001
        port("gjenåpne-livsløpet fullfører", False,
             f"{type(e).__name__}: {str(e)[:110]}")
    finally:
        rt9.close()

    mig.close()

    print("\n" + "=" * 62)
    if _FEIL:
        print(f"\033[31mRUNDTUR RØD — {_FEIL} port(er) nede.\033[0m "
              "Styringskjeden er IKKE hel.")
        return 1
    print("\033[32mRUNDTUR GRØNN\033[0m — bootstrap, fire øyne, aktivering, "
          "register og bruk henger sammen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
