"""M-35 øvelseslogikken — rene, testbare målinger (089, dommene 1–5).

Modulen regner; den kobler ALDRI selv. All I/O (basen, statusfilen,
/live-socketen) eies av bindingen (`deploy/staging/kjor-m35-ovelse.py`
i v1; arbeideren i PR-B), som rekker funksjonene her ferdigleste verdier
og injiserbare stier. Det er nøyaktig det som gjør «statusfil
fraværende/foreldet → rødt funn» målbar i en pytest uten rot-rettigheter
og uten en levende backupkatalog.

De tre dommene som styrer formen:

* Dom 4: statusfilen fra backup-db.sh er ENESTE kilde til RTO/RPO-
  evidensen — aldri journal-parsing, aldri en egen restore. Fraværende,
  uparsbar eller foreldet fil er et RØDT funn; grønt uten evidens
  finnes ikke.
* Dom 5: tallet heter det det er — `maalt_restoretid_s` er
  restore-til-ISOLERT-BASE-proxyen. Full tjeneste-RTO (selvrevers-
  øvelsen) er v2, og ingenting her later som noe annet.
* Dom 2: rytmen er månedlig; `siste_gronne_alder_dogn` bærer den, og
  kvartalsgulvet (92 døgn) håndheves av `m35-v1`-grensen, ikke her.
"""
from __future__ import annotations

import json
from pathlib import Path

#: 2 døgn: eldre evidens enn to nattlige kjøringer betyr at minst én
#: kjøring har feilet — det er et funn, aldri grønt (samme tall som
#: `rpo_maks_backupalder_s` i KRAVGRENSER m35-v1; grensen er portens,
#: dette er øvelsens eget «foreldet»-gulv for statusFILEN).
MAKS_STATUSFIL_ALDER_S = 172_800

#: Kontaktdekningen (planens §4): en bekreftelse eldre enn dette er en
#: kontakt ingen har prøvd på et kvartal — dekningen teller den ikke.
MAKS_BEKREFTELSESALDER_S = 90 * 86_400


def vurder_statusfil(sti: Path | str, now_s: float,
                     maks_alder_s: int = MAKS_STATUSFIL_ALDER_S) -> dict:
    """Les og døm backupskriptets statusfil (dom 4). Fail-closed.

    -> {"restore_verifisert": bool, "maalt_restoretid_s": float | None,
        "maalt_backupalder_s": float | None, "funn": [ ... ]}

    Enhver vei som ikke er «fersk fil, gyldige tall» gir
    restore_verifisert=False, None-målinger og et rødt funn med egen
    tekstnøkkel — flaten og rapporten sier HVORFOR det er rødt, aldri
    bare at det er det.
    """
    sti = Path(sti)
    try:
        raa = sti.read_bytes()
    except OSError:
        return {"restore_verifisert": False, "maalt_restoretid_s": None,
                "maalt_backupalder_s": None,
                "funn": [{"alvor": "rodt",
                          "tekstnokkel": "kontinuitet.funn.statusfil_mangler"}]}
    try:
        data = json.loads(raa.decode("utf-8"))
        ts = float(data["ts"])
        backup_ts = float(data["backup_ts"])
        restore_s = float(data["restore_varighet_s"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return {"restore_verifisert": False, "maalt_restoretid_s": None,
                "maalt_backupalder_s": None,
                "funn": [{"alvor": "rodt",
                          "tekstnokkel": "kontinuitet.funn.statusfil_uleselig"}]}
    # bool er subklasse av int/float-veien; NaN/inf gjør enhver
    # sammenligning usann (manifestskjema-lærdommen) — fail-closed.
    if any(isinstance(v, bool) for v in
           (data.get("ts"), data.get("backup_ts"),
            data.get("restore_varighet_s"))) \
            or any(v != v or v in (float("inf"), float("-inf"))
                   for v in (ts, backup_ts, restore_s)) \
            or restore_s <= 0 or backup_ts > now_s or ts > now_s:
        return {"restore_verifisert": False, "maalt_restoretid_s": None,
                "maalt_backupalder_s": None,
                "funn": [{"alvor": "rodt",
                          "tekstnokkel": "kontinuitet.funn.statusfil_uleselig"}]}
    alder = now_s - backup_ts
    if alder > maks_alder_s:
        # Filen ER lesbar og verifiseringen VAR ekte — men evidensen er
        # for gammel til å bære et grønt: restore_verifisert er en
        # påstand om NÅ-tilstanden, ikke om en fortid.
        return {"restore_verifisert": False,
                "maalt_restoretid_s": restore_s,
                "maalt_backupalder_s": alder,
                "funn": [{"alvor": "rodt",
                          "tekstnokkel": "kontinuitet.funn.statusfil_foreldet",
                          "detalj": {"alder_s": round(alder)}}]}
    return {"restore_verifisert": True, "maalt_restoretid_s": restore_s,
            "maalt_backupalder_s": alder, "funn": []}


def vurder_kart(tjenester, referent_finnes) -> dict:
    """Kartferskheten: hver KRITISK rad må peke på en referent som
    fortsatt finnes.

    `tjenester`: iterably av kartrader på registerets egen form
    (tjeneste_id, referent_type, referent_id, kritikalitet,
    kontaktrolle) — SAMME rad som `vurder_kontakter` leser, slik at ett
    spørring mot `kontinuitet_tjeneste` mater begge målingene og de
    aldri kan dømme på hvert sitt utvalg.
    `referent_finnes(referent_type, referent_id)` ->
    True/False/None — None betyr «uverifiserbar herfra» (typen
    `ekstern` har ingen lokal fasit), og telles som et GULT funn på en
    kritisk rad, aldri som brudd: øvelsen sier ærlig hva den ikke kan
    måle i stedet for å gjette grønt eller rope rødt.
    """
    forsok = brudd = 0
    funn = []
    for rad in tjenester:
        tjeneste_id, referent_type, referent_id, kritikalitet = rad[:4]
        if kritikalitet != "kritisk":
            continue
        svar = referent_finnes(referent_type, referent_id)
        if svar is None:
            funn.append({"alvor": "gult",
                         "tekstnokkel":
                             "kontinuitet.funn.referent_uverifiserbar",
                         "detalj": {"tjeneste_id": str(tjeneste_id),
                                    "referent_type": referent_type}})
            continue
        forsok += 1
        if not svar:
            brudd += 1
            funn.append({"alvor": "rodt",
                         "tekstnokkel": "kontinuitet.funn.referent_borte",
                         "detalj": {"tjeneste_id": str(tjeneste_id),
                                    "referent_type": referent_type,
                                    "referent_id": referent_id}})
    return {"forsok": forsok, "brudd": brudd, "funn": funn}


def vurder_kontakter(tjenester, kontakter, now_s: float,
                     maks_alder_s: int = MAKS_BEKREFTELSESALDER_S) -> dict:
    """Kontaktdekningen: hver KRITISK tjenestes kontaktrolle må ha
    minst én kontakt med bekreftelse ferskere enn 90 døgn.

    `kontakter`: iterably av (rolle, bekreftet_ts_epoch | None).
    Rollene måles som MENGDE (én rolle = ett forsøk, uansett hvor mange
    kritiske tjenester som peker på den): funnet er «rollen er udekket»,
    og å telle den fem ganger gjør ikke hullet fem ganger større.
    """
    ferske: set[str] = set()
    for rolle, bekreftet_ts in kontakter:
        if bekreftet_ts is not None \
                and now_s - float(bekreftet_ts) <= maks_alder_s:
            ferske.add(rolle)
    kritiske_roller: set[str] = set()
    for rad in tjenester:
        kritikalitet, kontaktrolle = rad[3], rad[4]
        if kritikalitet == "kritisk":
            kritiske_roller.add(kontaktrolle)
    forsok = len(kritiske_roller)
    udekkede = sorted(kritiske_roller - ferske)
    funn = [{"alvor": "rodt",
             "tekstnokkel": "kontinuitet.funn.kontaktrolle_udekket",
             "detalj": {"rolle": rolle}} for rolle in udekkede]
    return {"forsok": forsok, "brudd": len(udekkede), "funn": funn}


def bygg_rapport(*, tenant: str, commit: str, vert: str, ts_iso: str,
                 statusfil: dict, kart: dict, kontakter: dict,
                 live_ok: bool, tidslinje_forsok: int,
                 tidslinje_brudd: int, lukking_forsok: int,
                 lukking_brudd: int, siste_gronne_alder_dogn,
                 ddl_begge_gronne: bool, axe_forsok: int = 0,
                 axe_brudd: int = 0, ekstra_funn=()) -> dict:
    """Sett sammen artefaktet etter artefakt-m35-skjema.json.

    `bestatt` REGNES her, aldri påstås: grønn er null røde funn, null
    brudd i hvert målt par, verifisert restore OG levende /live. Gule
    funn (uverifiserbare referenter, ingen tidligere øvelse) farger
    ikke dommen — de står i rapporten for mennesket som leser den.
    """
    funn = list(statusfil["funn"]) + list(kart["funn"]) \
        + list(kontakter["funn"]) + list(ekstra_funn)
    if not live_ok:
        funn.append({"alvor": "rodt",
                     "tekstnokkel": "kontinuitet.funn.live_helse_feilet"})
    if siste_gronne_alder_dogn is None:
        funn.append({"alvor": "gult",
                     "tekstnokkel":
                         "kontinuitet.funn.ingen_tidligere_ovelse"})
    rode = [f for f in funn if f["alvor"] == "rodt"]
    bestatt = (not rode
               and statusfil["restore_verifisert"]
               and live_ok
               and kart["brudd"] == 0 and kontakter["brudd"] == 0
               and tidslinje_brudd == 0 and lukking_brudd == 0
               and axe_brudd == 0)
    return {
        "krav_id": "m35-v1",
        "ts": ts_iso,
        "bestatt": bestatt,
        "oppsett": {"modul": "m35_kontinuitet", "commit": commit,
                    "vert": vert, "tenant": tenant},
        "maalt": {
            "kart_kritisk_uten_referent_forsok": kart["forsok"],
            "kart_kritisk_uten_referent_brudd": kart["brudd"],
            "kontakt_kritisk_rolle_ubekreftet_90d_forsok":
                kontakter["forsok"],
            "kontakt_kritisk_rolle_ubekreftet_90d_brudd":
                kontakter["brudd"],
            "hendelse_tidslinjepost_endret_forsok": tidslinje_forsok,
            "hendelse_tidslinjepost_endret_brudd": tidslinje_brudd,
            "hendelse_lukket_uten_etteranalyse_forsok": lukking_forsok,
            "hendelse_lukket_uten_etteranalyse_brudd": lukking_brudd,
            "ui_axe_alvorlige_brudd_forsok": axe_forsok,
            "ui_axe_alvorlige_brudd_brudd": axe_brudd,
            "restore_verifisert": statusfil["restore_verifisert"],
            "ddl_begge_kjoringer_gronne": ddl_begge_gronne,
            "maalt_restoretid_s": statusfil["maalt_restoretid_s"],
            "maalt_backupalder_s":
                (None if statusfil["maalt_backupalder_s"] is None
                 else round(statusfil["maalt_backupalder_s"], 3)),
            "siste_gronne_alder_dogn": siste_gronne_alder_dogn,
            "live_helse_ok": live_ok,
        },
        "funn": funn,
    }
