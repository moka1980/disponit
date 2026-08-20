#!/usr/bin/env python3
"""Avleder `wcag-kontroll-v1`-artefaktet MEKANISK av evidens.jsonl.

Evidensfila er rundens fulle historie: idempotente gjenkjøringer, røde
iterasjoner som ble reparert, og replays som ikke målte noe. Sammendraget
her er derfor ikke «velg de fine tallene» — det er ett sett NAVNGITTE
utvalgsregler som kan leses, angripes og kjøres om igjen:

  * En MÅLING er den SISTE hendelsen av sin type: runden er idempotent,
    og siste tilstand er rundens tilstand. Unntaket er
    `feilinjisering_motorfeil` med `utfall: "tomt"`: et claim som ikke
    fant noe å feilinjisere målte ingenting, og velges aldri — den siste
    FAKTISKE injeksjonen gjelder.
  * Hver hendelse bærer KONTEKSTEN den ble målt i: release og
    image-digest fra siste `fase2_ok`/`fase1_ok` FØR den i den
    append-only fila. Alle de valgte hendelsene må dele den konteksten —
    ellers er sammendraget ikke ett sett tall fra én kjøring, og
    genereringen stopper. Fasitrunden gikk på wcag-r1; alle m56-releasene
    deler digest, så tallene gjelder nøyaktig de aksepterte bytene (A1).
  * `bestatt` regnes ut av de VALGTE hendelsenes egne `ok`-felter —
    aldri påstått.
  * FRISTTELLINGEN REGNES UT AV `kjoring`-LINJENE, ikke lest av
    `fase5_resultat`s summeringsstreng. Et gjenspill setter
    `utfall: "utfort"` og `varighet_s: null` uten å måle fristen på
    nytt, så summen kan bli «10/10» av rapporter som opprinnelig brøt
    fristen. Bare linjer med rent utfall, null avvik og en MÅLT varighet
    under sin egen frist telles — og spriker linjene fra summen, skrives
    det ikke noe sammendrag i det hele tatt.

Deterministisk med vilje: samme fil inn gir byte-identisk artefakt ut
(ingen klokkelesing), så CI kan regenerere og sammenligne.

BRUK: python3 deploy/staging/wcag-kontroll-artefakt.py \
        deploy/staging/artefakter/evidens-wcag-runde-20260818.jsonl \
        [--ut artefakt.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPOROT = Path(__file__).resolve().parents[2]

#: Modulens identitet er REGISTERETS (`modulhode.modul_id`), ikke
#: katalognavnet. Codex' P2 på PR #117: artefaktet kalte seg
#: `m56_wcag_audit` — mappen det ble kjørt fra — mens drillartefaktet,
#: staging-runneren, akseptskriptet og 049-radene alle bruker
#: `m_wcag_audit`. Et sammendrag som navngir en annen modul enn den som
#: aksepteres, er evidens for feil ting, og skjemaets «ikke-tom streng»
#: fanget det aldri.
MODUL = "m_wcag_audit"


def les(sti: Path) -> list[dict]:
    return [json.loads(l) for l in sti.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def kontekster(rader: list[dict]) -> list[tuple[str | None, str | None]]:
    """Per hendelse: (image_digest, release) som gjaldt DA den ble målt.

    Fila er append-only, så konteksten til hendelse nr. i er den siste
    `fase1_ok`/`fase2_ok` FØR i. Posisjon, ikke `ts`: rekkefølgen i fila
    ER rekkefølgen hendelsene ble skrevet, og to hendelser kan dele
    tidsstempel.
    """
    ut: list[tuple[str | None, str | None]] = []
    image = release = None
    for d in rader:
        if d.get("hendelse") == "fase1_ok":
            image = str(d.get("image_id") or "").removeprefix("sha256:")
        elif d.get("hendelse") == "fase2_ok":
            release = d.get("release")
        ut.append((image, release))
    return ut


def en_kjoring(rader: list[dict], kontekst: list, valgte: list[int]) -> tuple:
    """Alle de valgte målingene må dele kontekst. -> (digest, release).

    Codex' P1 på PR #117 (runde 4): hver `siste()` plukket sin hendelse
    UAVHENGIG, mens release og image ble hentet fra konteksten før siste
    `fase5_resultat`. En delvis gjenkjøring av bare robots, frekvens,
    motor eller feilinjisering — eventuelt etter et release-/imagebytte —
    ble derfor satt sammen med et eldre fase 5-resultat og TILSKREVET den
    eldre digesten. Alle `ok`-feltene kunne stå grønne uten at noen
    enkelt kjøring hadde produsert det tallsettet.

    Sammendraget er evidens for ETT image: da må hver måling i det være
    gjort på nettopp de bytene, i den releasen. Fail-closed — et
    sammendrag som ikke kan avledes fra én sammenhengende kontekst,
    skrives ikke.
    """
    sett = {kontekst[i] for i in valgte}
    if len(sett) != 1:
        linjer = sorted(
            f"{rader[i].get('hendelse')}: release={kontekst[i][1]!r}"
            f" image={(kontekst[i][0] or '')[:12]}…" for i in valgte)
        raise SystemExit(
            "AVBRUTT: de valgte målingene er gjort i ULIKE kjøringer —\n  "
            + "\n  ".join(linjer)
            + "\nsammendraget ville tilskrevet ett image tall som er målt"
              " på et annet")
    digest, release = sett.pop()
    if not digest or not release:
        raise SystemExit("AVBRUTT: målingene mangler kontekst (fase1_ok/"
                         "fase2_ok før seg) — uten release og image er"
                         " tallene ikke evidens for noe")
    return digest, release


def fase5_linjene(rader: list[dict], kontekst: list,
                  i_fase5: int) -> list[dict]:
    """`kjoring`-linjene DEN valgte fase 5-summen er summen av.

    Fila er append-only og fase 5 kan kjøres flere ganger: linjene som
    hører til summen, er de som står mellom forrige `fase5_resultat` og
    denne — i den samme konteksten (release + image). Posisjon, ikke
    `ts`, av samme grunn som i `kontekster`.
    """
    start = 0
    for j in range(i_fase5 - 1, -1, -1):
        if rader[j].get("hendelse") == "fase5_resultat":
            start = j + 1
            break
    return [rader[j] for j in range(start, i_fase5)
            if rader[j].get("hendelse") == "kjoring"
            and kontekst[j] == kontekst[i_fase5]]


def signert_innen_frist(linjer: list[dict]) -> int:
    """Teller kjøringene som FAKTISK ble målt signert innen fristen.

    Codex' P2 på PR #117 (runde 10): tallet ble kopiert ordrett fra
    `fase5_resultat`s summeringsstreng. Men fase 5 er idempotent, og på
    et gjenspill (`alt_utfort`) er rapporten alt lesbar: da settes
    `utfall: "utfort"` uten at motoren kjøres, `varighet_s` er `None`, og
    linjas `ok` blir grønn UTEN at fristen er målt på nytt. Ti rapporter
    som opprinnelig BRØT fristen kunne derfor bli «10/10» ved neste
    gjenkjøring, og gå rett gjennom evidensporten.

    Her telles bare linjer som bærer sin egen måling: et rent utfall
    (`utfort`), null avvik mot fasiten, og en varighet som ER målt og
    ligger under den fristen linja selv oppgir. Et gjenspill teller ikke
    — ikke fordi kjøringen var dårlig, men fordi den linja ikke MÅLTE
    noe. `i` avduplikeres, så en delvis gjenkjøring aldri kan telle en
    posisjon to ganger.
    """
    gronne = set()
    for d in linjer:
        varighet, frist = d.get("varighet_s"), d.get("frist_s")
        if (d.get("utfall") == "utfort"
                and not int(d.get("avvik_mot_fasit") or 0)
                and isinstance(varighet, (int, float))
                and isinstance(frist, (int, float))
                and varighet < frist):
            gronne.add(d.get("i"))
    return len(gronne)


def sammendrag(rader: list[dict], kilde: str, kilde_sha256: str) -> dict:
    kontekst = kontekster(rader)

    def siste(navn: str, kandidat=lambda d: True) -> int:
        valgte = [i for i, d in enumerate(rader) if d.get("hendelse") == navn
                  and kandidat(d)]
        if not valgte:
            raise SystemExit(f"AVBRUTT: ingen {navn!r} i evidensfila — "
                             "runden er ufullstendig, ikke grønn")
        return valgte[-1]

    indekser = [
        siste("fase5_resultat"),
        siste("port20_robots"),
        siste("port20_robots_5xx"),
        siste("port21_frekvens"),
        siste("port24_motormiljo"),
        siste("feilinjisering_motorfeil", lambda d: d.get("utfall") != "tomt"),
        siste("feilinjisering_evidensfrist"),
    ]
    image_digest, release = en_kjoring(rader, kontekst, indekser)
    valgte = tuple(rader[i] for i in indekser)
    (fase5, robots, robots5, frekv, motor, injeksjon, frist) = valgte

    påstått, krav = (int(x) for x in
                     str(fase5["ti_kjoringer_signert_innen_frist"]).split("/"))
    # TALLET REGNES UT AV LINJENE, ikke lest av summen (Codex P2, runde
    # 10). Og spriker de to, er evidensfila i strid med seg selv: summen
    # påstår flere målte kjøringer enn linjene bærer, og da skrives det
    # ikke noe sammendrag i det hele tatt.
    signert = signert_innen_frist(fase5_linjene(rader, kontekst, indekser[0]))
    if signert != påstått:
        raise SystemExit(
            f"AVBRUTT: `fase5_resultat` påstår {påstått}/{krav} signert"
            f" innen frist, men bare {signert} av `kjoring`-linjene i den"
            " runden bærer et rent utfall, null avvik OG en målt varighet"
            " under sin egen frist. Et gjenspill setter utfall=utfort og"
            " varighet_s=null uten å måle fristen på nytt, så summen kan"
            " ikke være kilden. Kjør fase 5 på nytt (nye"
            " idempotensnøkler) og la kjøringene måle seg selv.")
    return {
        "krav_id": "wcag-kontroll-v1",
        "ts": max(d["ts"] for d in valgte),
        "bestatt": all(d.get("ok") is True for d in valgte),
        "oppsett": {
            "modul": MODUL,
            # Konteksten ALLE de valgte målingene deler — ikke den som
            # tilfeldigvis gjaldt ved én av dem (Codex P1, runde 4).
            "release": release,
            "image_digest": image_digest,
            "kilde": kilde,
            # SP-11 hele veien: manifestet hash-binder DETTE artefaktet,
            # og artefaktet hash-binder råfilen det er avledet av — et
            # bytte av evidens.jsonl bryter kjeden i CI, ikke i en lesning.
            "kilde_sha256": kilde_sha256,
        },
        "maalt": {
            "kjoringer_signert_innen_frist": signert,
            "kjoringer_krav": krav,
            "avvik_mot_fasit": int(fase5["funn_avvik_mot_fasit"]),
            "robots_private_forisporsler": int(robots["privat_forisporsler"]),
            "robots_5xx_sider_kontrollert": int(robots5["sider_kontrollert"]),
            "robots_5xx_krav": int(robots5["krav"]),
            "frekvens_tillat": sum(1 for u in frekv["utfall"]
                                   if u == "tillat"),
            "frekvens_avvist_over_grense": sum(1 for u in frekv["utfall"]
                                               if u != "tillat"),
            "egress_lekkasjer": len(motor["lekkasjer"]),
            "feilinjisering_feilet_med_kvittering":
                1 if (injeksjon["oppdragstatus"] == "feilet"
                      and injeksjon["har_kvittering"]) else 0,
            "feilinjisering_promoterte_artefakter":
                int(injeksjon["promoterte_artefakter"]),
            "evidensfrist_reapet": len(frist["reapet"]),
            "evidensfrist_sak_opprettet": 1 if frist.get("sak") else 0,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("evidens", type=Path)
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    try:
        kilde = str(a.evidens.resolve().relative_to(REPOROT))
    except ValueError:
        kilde = a.evidens.name
    art = sammendrag(les(a.evidens), kilde,
                     hashlib.sha256(a.evidens.read_bytes()).hexdigest())
    tekst = json.dumps(art, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if a.ut:
        a.ut.write_text(tekst, encoding="utf-8")
        print(f"skrev {a.ut} (bestatt={art['bestatt']})")
    else:
        sys.stdout.write(tekst)
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
