"""Validering av modulmanifester mot manifest-skjema.json (v3-delta pkt. 7).

Registeret (`registry.py`) leser manifester for å bestemme avhengigheter og
aktivering. Det bryr seg ikke om staging-sjekklisten. Sjekklisten er
derimot den ENESTE maskinlesbare kilden til om en modul faktisk er bevist
klar — og uten et skjema er «ja» og «nei» fritekst som kan endres til
hva som helst uten at noe protesterer.

Kjøres i CI. Kaster aldri: feilformet manifest gir feilliste, ikke
exception — samme kontrakt som `policy_validator.schema.valider_policy`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SKJEMA_STI = Path(__file__).resolve().parent / "manifest-skjema.json"
REPOROT = Path(__file__).resolve().parents[2]

#: Grensene ytelsesporten faktisk krever (v2 Del 6). De står HER, som data
#: CI leser, og ikke bare i et manifestnotat: et tall i en kommentar kan
#: ikke gjøre en kjøring rød.
KRAVGRENSER: dict[str, dict] = {
    "perf-m01-v1": {
        "min_antall": 6000,
        "maks_feil": 0,
        "maks_rate_begrenset": 0,
        "maks_p95_ms": 150.0,
        "krev_en_til_en": True,
        "krev_routing": True,
    },
}


def _skjema() -> dict:
    return json.loads(SKJEMA_STI.read_text(encoding="utf-8"))


def valider_manifest(manifest: object) -> list[str]:
    """Tom liste == gyldig."""
    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(_skjema())
        return sorted(
            f"{'/'.join(str(p) for p in e.absolute_path) or '<rot>'}: {e.message}"
            for e in validator.iter_errors(manifest))
    except Exception as e:  # siste skanse — aldri ukontrollert exception
        return [f"intern valideringsfeil ({type(e).__name__}): {e}"]


def valider_alle(modulrot: Path) -> dict[str, list[str]]:
    """-> {modul-id: feilliste}. Alle nøkler med tom liste == alt gyldig."""
    import yaml
    ut: dict[str, list[str]] = {}
    for fil in sorted(Path(modulrot).glob("*/manifest.yaml")):
        data = yaml.safe_load(fil.read_text(encoding="utf-8"))
        ut[fil.parent.name] = valider_manifest(data)
    return ut


def _les_artefakt(sti: Path) -> tuple[dict | None, str | None, str]:
    """-> (innhold, sha256, feilmelding). Åpner og hasher i ETT lesesteg.

    Leses filen to ganger — én gang for hashen og én for innholdet — er det
    i prinsippet to forskjellige filer som valideres. Her hashes nøyaktig de
    bytene som deretter tolkes.
    """
    try:
        raa = sti.read_bytes()
    except OSError as e:
        return None, None, f"artefaktet kan ikke åpnes: {type(e).__name__}"
    sha = hashlib.sha256(raa).hexdigest()
    try:
        data = json.loads(raa.decode("utf-8"))
    except Exception as e:
        return None, sha, f"artefaktet er ikke gyldig JSON ({type(e).__name__})"
    if not isinstance(data, dict):
        return None, sha, "artefaktet er ikke et JSON-objekt"
    return data, sha, ""


def _sjekk_grenser(krav_id: str, art: dict) -> list[str]:
    """Håndhever KRAVGRENSER mot tallene i artefaktet.

    Dette er selve poenget med porten: `bestatt: true` inne i artefaktet er
    produsentens EGEN påstand. Uten en uavhengig kontroll av tallene ville
    en kjøring som skrev `bestatt: true` over 6 000 feilsvar passert.
    """
    grense = KRAVGRENSER.get(krav_id)
    if grense is None:
        return [f"ukjent krav_id {krav_id!r} — ingen grenser å håndheve"]
    feil: list[str] = []
    if art.get("krav_id") != krav_id:
        feil.append(f"artefaktet gjelder {art.get('krav_id')!r}, "
                    f"manifestet påstår {krav_id!r}")
    if art.get("bestatt") is not True:
        feil.append("artefaktet sier ikke bestatt: true")

    m = art.get("maalt")
    if not isinstance(m, dict):
        return feil + ["artefaktet mangler `maalt`"]

    antall = m.get("antall")
    if not isinstance(antall, int) or antall < grense["min_antall"]:
        feil.append(f"antall={antall}, krever >= {grense['min_antall']}")
    for felt, tak in (("feil", grense["maks_feil"]),
                      ("rate_begrenset", grense["maks_rate_begrenset"])):
        verdi = m.get(felt)
        if not isinstance(verdi, int) or verdi > tak:
            feil.append(f"{felt}={verdi}, krever <= {tak}")
    if m.get("feiltyper"):
        feil.append(f"artefaktet har feiltyper: {m.get('feiltyper')}")

    svartid = m.get("svartid_ms")
    p95 = svartid.get("p95") if isinstance(svartid, dict) else None
    if not isinstance(p95, (int, float)) or p95 >= grense["maks_p95_ms"]:
        feil.append(f"p95={p95} ms, krever < {grense['maks_p95_ms']} ms")

    k = art.get("etterkontroll")
    if not isinstance(k, dict):
        feil.append("artefaktet mangler `etterkontroll`")
    else:
        if grense["krev_en_til_en"]:
            if k.get("en_til_en") is not True:
                feil.append("etterkontroll: en_til_en er ikke true")
            svar, rader = k.get("auditerte_svar"), k.get("revisjonsrader")
            if svar != rader or not isinstance(svar, int) or svar < grense["min_antall"]:
                feil.append(f"auditerte_svar={svar} vs revisjonsrader={rader}, "
                            f"krever like og >= {grense['min_antall']}")
        if grense["krev_routing"] and k.get("routing_stemmer") is not True:
            feil.append("etterkontroll: routing_stemmer er ikke true")
    return feil


def valider_artefakter(manifest: dict, rot: Path | None = None) -> list[str]:
    """Håndhever evidenskjeden for hvert `ja` med krav_id. Tom liste == ok.

    Codex' P1 på PR #8: skjemaet krevde bare at `artefakt` var en ikke-tom
    STRENG. `artefakt: tull.json` passerte da like fint som en ekte måling,
    og hashen alene beviser bare at noen kjenner en streng. Her åpnes filen
    faktisk, hashen verifiseres mot innholdet, formatet valideres og
    tallene måles mot KRAVGRENSER.
    """
    rot = Path(rot) if rot is not None else REPOROT
    sjekkliste = (manifest or {}).get("staging_sjekkliste") or {}
    feil: list[str] = []
    for navn, p in sorted(sjekkliste.items()):
        if not isinstance(p, dict) or p.get("status") != "ja":
            continue
        krav_id = p.get("krav_id")
        if not krav_id:
            continue                      # ja uten krav_id krever ikke artefakt
        sti_tekst = p.get("artefakt")
        forventet = p.get("artefakt_sha256")
        if not sti_tekst or not forventet:
            feil.append(f"{navn}: ja med krav_id mangler artefakt/artefakt_sha256")
            continue
        sti = (rot / sti_tekst).resolve()
        try:
            sti.relative_to(rot.resolve())
        except ValueError:
            feil.append(f"{navn}: artefaktstien peker utenfor repoet")
            continue
        data, sha, melding = _les_artefakt(sti)
        if melding:
            feil.append(f"{navn}: {melding} ({sti_tekst})")
            continue
        if sha != forventet:
            feil.append(f"{navn}: sha256 stemmer ikke — manifestet sier "
                        f"{forventet[:12]}…, filen er {sha[:12]}…")
            continue
        feil += [f"{navn}: {m}" for m in _sjekk_grenser(krav_id, data)]
    return feil


def uavklarte_punkter(manifest: dict) -> list[str]:
    """Sjekklistepunkter som IKKE er `ja`.

    Regelen som aldri fravikes (RUTINER pkt. 2): en modul settes ikke til
    `aktiv` før alle punkter er ja. Funksjonen gjør regelen målbar i stedet
    for å be noen huske den.
    """
    sjekkliste = (manifest or {}).get("staging_sjekkliste") or {}
    return sorted(navn for navn, p in sjekkliste.items()
                  if not isinstance(p, dict) or p.get("status") != "ja")


def aktiv_uten_bevis(manifest: dict) -> list[str]:
    """Tom liste med mindre modulen er `aktiv` OG har uavklarte punkter."""
    if (manifest or {}).get("status") != "aktiv":
        return []
    return uavklarte_punkter(manifest)
