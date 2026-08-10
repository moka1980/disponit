"""KLASSIFIKATOR_V1 (PR-013) — klassifiserer en policy-endring som
UTVIDER / INNSNEVRER / NØYTRAL.

Å endre policy er å endre agentens fullmakter. Klassifikatoren avgjør om en
endring UTVIDER fullmakten (krever fire øyne) eller ikke.

Bindende egenskaper (v2 §1, v3, v4):
- **Skjemadrevet, ikke diff-drevet:** hver muterbar leaf-path i skjema v0.2
  har NØYAKTIG én regel. En schema-leaf uten regel → byggefeil (CI-port);
  en regel mot en leaf som ikke finnes → byggefeil.
- **Fail-closed:** ukjent sti/type/uklassifiserbar sammenligning → UTVIDER.
- **Mengde vs. ordnet** er deklarert her (nøkkelfelt for arrays).
- **Frekvens = burst-fullmakt:** INNSNEVRER kun ved uendret vindu+scope OG
  redusert `maks`; alt annet UTVIDER.
- **Tidsvindu = mengdeinklusjon** over en kanonisk uke i lokale ukeminutter,
  via `tidsvindu.tillatte_ukeminutter` — SAMME kodevei som motoren bruker
  (aldri et rekursivt motorkall). Tidssoneendring → UTVIDER.
- **metadata** klassifiseres NØYTRAL (strippes før motoren; v4 §4).
- Samlet klasse = strengeste enkeltendring (UTVIDER > INNSNEVRER > NØYTRAL).
"""
from __future__ import annotations

import hashlib
import json

from . import tidsvindu

KLASSIFIKATORVERSJON = "kv1"

UTVIDER = "UTVIDER"
INNSNEVRER = "INNSNEVRER"
NØYTRAL = "NØYTRAL"
_RANG = {NØYTRAL: 0, INNSNEVRER: 1, UTVIDER: 2}

# Ordnede gitre: indeks lav = «strengeste/sterkeste vakt», høy = «svakest».
# Å bevege seg mot svakere vakt (høyere indeks) UTVIDER fullmakten.
_MODUS = ["alltid_stopp", "auto_med_vilkaar", "auto"]          # auto = svakest
_VED_BRUDD = ["unntakskø", "stopp_og_varsle", "frys"]          # frys = mildest/svakest
_REVERSERING = ["irreversibel", "kompenserende", "direkte"]    # direkte = svakest vakt

_FRAVÆR = object()   # markør for «feltet finnes ikke»


# ---------------------------------------------------------------------------
# Byggeklosser
# ---------------------------------------------------------------------------
def _skalar_opp(gammel, ny) -> str:
    """En verdi som stiger (eller fjernes → ubegrenset) UTVIDER; synker (eller
    settes fra fravær) INNSNEVRER. For beløpsgrenser o.l."""
    if gammel == ny:
        return NØYTRAL
    if ny is _FRAVÆR:                     # grensen fjernet → ubegrenset
        return UTVIDER
    if gammel is _FRAVÆR:                 # grense lagt til
        return INNSNEVRER
    try:
        from decimal import Decimal
        g, n = Decimal(str(gammel)), Decimal(str(ny))
    except Exception:
        return UTVIDER                    # ikke sammenlignbar → fail-closed
    return UTVIDER if n > g else INNSNEVRER


def _gitter(rekke: list[str], gammel, ny) -> str:
    """Ordnet enum: mot høyere indeks (svakere vakt) UTVIDER."""
    if gammel == ny:
        return NØYTRAL
    try:
        gi = rekke.index(gammel) if gammel is not _FRAVÆR else -1
        ni = rekke.index(ny) if ny is not _FRAVÆR else -1
    except ValueError:
        return UTVIDER                    # ukjent enumverdi → fail-closed
    if gi == ni:
        return NØYTRAL
    return UTVIDER if ni > gi else INNSNEVRER


def _mengde(gammel, ny) -> str:
    """Uordnet mengde (av hashbare nøkler): element lagt til UTVIDER, fjernet
    INNSNEVRER, begge → UTVIDER (strengeste)."""
    g = set(gammel) if gammel is not _FRAVÆR else set()
    n = set(ny) if ny is not _FRAVÆR else set()
    if g == n:
        return NØYTRAL
    lagt_til = bool(n - g)
    fjernet = bool(g - n)
    if lagt_til and fjernet:
        return UTVIDER
    return UTVIDER if lagt_til else INNSNEVRER


def _mengde_invers(gammel, ny) -> str:
    """Invertert mengde: element FJERNET UTVIDER (færre krav = mer permissivt),
    LAGT TIL INNSNEVRER. For `vilkaar` — motsatt av `handlinger`/`roller`."""
    g = set(gammel) if gammel is not _FRAVÆR else set()
    n = set(ny) if ny is not _FRAVÆR else set()
    if g == n:
        return NØYTRAL
    lagt_til = bool(n - g)
    fjernet = bool(g - n)
    if lagt_til and fjernet:
        return UTVIDER
    return UTVIDER if fjernet else INNSNEVRER


def _bool_opp(gammel, ny) -> str:
    """`false → true` UTVIDER (mer fullmakt), `true → false` INNSNEVRER."""
    g = bool(gammel) if gammel is not _FRAVÆR else False
    n = bool(ny) if ny is not _FRAVÆR else False
    if g == n:
        return NØYTRAL
    return UTVIDER if (n and not g) else INNSNEVRER


def _bool_ned(gammel, ny) -> str:
    """`true → false` UTVIDER (fjerner et krav), `false → true` INNSNEVRER.
    For krav-flagg som `krever_fire_oyne`."""
    g = bool(gammel) if gammel is not _FRAVÆR else False
    n = bool(ny) if ny is not _FRAVÆR else False
    if g == n:
        return NØYTRAL
    return UTVIDER if (g and not n) else INNSNEVRER


def _valuta(gammel, ny) -> str:
    """Enhver valuta-endring → UTVIDER (kan ikke sammenlignes på tvers)."""
    return NØYTRAL if gammel == ny else UTVIDER


def _tidssone(gammel, ny) -> str:
    return NØYTRAL if gammel == ny else UTVIDER


def _tidsvindu_klasse(gammel, ny, *, sone_gammel, sone_ny) -> str:
    """Mengdeinklusjon i kanonisk uke via DELT tidskodevei. Tidssoneendring
    håndteres separat (egen regel), men et vindu tolket i en annen sone er
    heller ikke sammenlignbart → fail-closed her også."""
    if gammel == ny and sone_gammel == sone_ny:
        return NØYTRAL
    if gammel is _FRAVÆR:                 # nytt vindu innført = innsnevring
        return INNSNEVRER
    if ny is _FRAVÆR:                     # vindu fjernet = alltid tillatt
        return UTVIDER
    if sone_gammel != sone_ny:
        return UTVIDER                    # tidssoneendring → fail-closed
    try:
        g = tidsvindu.tillatte_ukeminutter(gammel, sone_gammel)
        n = tidsvindu.tillatte_ukeminutter(ny, sone_ny)
    except Exception:
        return UTVIDER
    if g == n:
        return NØYTRAL
    if n < g:                            # nytt ⊊ gammelt
        return INNSNEVRER
    if g < n:                            # gammelt ⊊ nytt
        return UTVIDER
    return UTVIDER                       # overlappende/ikke sammenlignbar


def _frekvens_klasse(gammel, ny) -> str:
    """Burst-fullmakt (v3 §1): INNSNEVRER KUN når periode_enhet, periode_antall
    OG grupperingsnokkel (scope) er UENDRET og `maks` er REDUSERT. Alt annet
    UTVIDER — inkl. fjernet frekvensgrense (ubegrenset)."""
    if gammel == ny:
        return NØYTRAL
    if gammel is _FRAVÆR:                 # frekvensgrense innført
        return INNSNEVRER
    if ny is _FRAVÆR:                     # frekvensgrense fjernet → ubegrenset
        return UTVIDER
    if not isinstance(gammel, dict) or not isinstance(ny, dict):
        return UTVIDER
    scope_likt = (gammel.get("periode_enhet") == ny.get("periode_enhet")
                  and gammel.get("periode_antall") == ny.get("periode_antall")
                  and gammel.get("grupperingsnokkel") == ny.get("grupperingsnokkel"))
    if not scope_likt:
        return UTVIDER                    # endret vindu/scope → burst kan endres
    gm, nm = gammel.get("maks"), ny.get("maks")
    if not isinstance(gm, int) or not isinstance(nm, int):
        return UTVIDER
    if nm == gm:
        return NØYTRAL
    return INNSNEVRER if nm < gm else UTVIDER


def _samlet(klasser) -> str:
    """Strengeste enkeltendring."""
    if not klasser:
        return NØYTRAL
    return max(klasser, key=lambda k: _RANG.get(k, _RANG[UTVIDER]))


def _hent(obj, *nokler):
    """Følg en nøkkelsti; -> verdi eller `_FRAVÆR`."""
    node = obj
    for k in nokler:
        if not isinstance(node, dict) or k not in node:
            return _FRAVÆR
        node = node[k]
    return node


# ---------------------------------------------------------------------------
# Regelsett per muterbar leaf-path (skjemadrevet — se test-porten som binder
# dette mot skjemaet). Nøkkelen er den normaliserte stien (`[]` = array-element).
# ---------------------------------------------------------------------------
#: Stier motoren ALDRI leser (semantikkfrie) → NØYTRAL. Hver må bevises
#: ikke-lest av motoren (statisk CI-port).
NØYTRALE_STIER = frozenset({
    "schema_version", "meta", "metadata",
    "handlinger[].id", "handlinger[].modul", "handlinger[].beskrivelse",
    "roller[].beskrivelse", "verifikatorer{}.beskrivelse",
    "verifikator_prioritet", "frister", "retention",
    "unntak.maks_auto_forsok", "unntak.eskalering",
    "dataklasser", "tidssone_beskrivelse",
})


def _klassifiser_handling(gammel_h, ny_h) -> list[str]:
    """Klassifiser endringer på ÉN handling (matchet på `id`)."""
    ut = []
    ut.append(_gitter(_MODUS, _hent(gammel_h, "modus"), _hent(ny_h, "modus")))
    ut.append(_gitter(_VED_BRUDD, _hent(gammel_h, "ved_brudd"),
                      _hent(ny_h, "ved_brudd")))
    ut.append(_gitter(_REVERSERING, _hent(gammel_h, "reversering", "type"),
                      _hent(ny_h, "reversering", "type")))
    ut.append(_skalar_opp(_hent(gammel_h, "grenser", "belop_maks"),
                          _hent(ny_h, "grenser", "belop_maks")))
    ut.append(_valuta(_hent(gammel_h, "grenser", "valuta"),
                      _hent(ny_h, "grenser", "valuta")))
    ut.append(_frekvens_klasse(_hent(gammel_h, "grenser", "frekvens"),
                               _hent(ny_h, "grenser", "frekvens")))
    ut.append(_mengde_invers(
        [v.get("navn") for v in (_liste(gammel_h, "vilkaar"))],
        [v.get("navn") for v in (_liste(ny_h, "vilkaar"))]))
    # tillatt_for/dataklasser_tillatt = mengder (fullmaktsrelevante)
    ut.append(_mengde(_liste(gammel_h, "tillatt_for"), _liste(ny_h, "tillatt_for")))
    ut.append(_mengde(_liste(gammel_h, "dataklasser_tillatt"),
                      _liste(ny_h, "dataklasser_tillatt")))
    return ut


def _liste(obj, key):
    v = _hent(obj, key)
    return v if isinstance(v, list) else []


def _indeks_paa(liste, nokkel):
    return {x.get(nokkel): x for x in liste if isinstance(x, dict)}


def klassifiser(gammel: dict, ny: dict) -> dict:
    """Klassifiser en policy-endring. -> {klasse, endringer, klassifikatorversjon,
    klassifisering_hash}. `gammel` = aktiv/base-policy (evt. DENY_ALL_V1), `ny`
    = utkastets innhold. Begge FORVENTES JCS-normaliserbare dicts."""
    endringer: list[dict] = []

    def legg(sti, klasse, gammel_v=None, ny_v=None):
        if klasse != NØYTRAL:
            endringer.append({"sti": sti, "klasse": klasse})

    sone_g = str(gammel.get("tidssone")) if isinstance(gammel, dict) else None
    sone_n = str(ny.get("tidssone")) if isinstance(ny, dict) else None

    # tidssone (top-level)
    legg("tidssone", _tidssone(_hent(gammel, "tidssone"), _hent(ny, "tidssone")))

    # roller[] (mengde på id)
    legg("roller[]", _mengde(
        [r.get("id") for r in _liste(gammel, "roller")],
        [r.get("id") for r in _liste(ny, "roller")]))

    # unntak.kategorier[] (mengde)
    legg("unntak.kategorier[]", _mengde(
        _hent(gammel, "unntak", "kategorier") if isinstance(_hent(gammel, "unntak", "kategorier"), list) else [],
        _hent(ny, "unntak", "kategorier") if isinstance(_hent(ny, "unntak", "kategorier"), list) else []))

    # verifikatorer{} (objekt-map på nøkkel)
    vg, vn = _hent(gammel, "verifikatorer"), _hent(ny, "verifikatorer")
    vg = vg if isinstance(vg, dict) else {}
    vn = vn if isinstance(vn, dict) else {}
    legg("verifikatorer{}", _mengde(vg.keys(), vn.keys()))
    for vid in set(vg) & set(vn):
        legg(f"verifikatorer{{}}[{vid}].betrodd_for[]", _mengde(
            _liste(vg[vid], "betrodd_for"), _liste(vn[vid], "betrodd_for")))
        legg(f"verifikatorer{{}}[{vid}].kan_fastsla_permanent", _bool_opp(
            _hent(vg[vid], "kan_fastsla_permanent"),
            _hent(vn[vid], "kan_fastsla_permanent")))

    # tidsvindu-regelen bruker sonene fra hver side.
    # handlinger[] (mengde på navn/id) + per-handling-felt
    hg = _indeks_paa(_liste(gammel, "handlinger"), "id")
    hn = _indeks_paa(_liste(ny, "handlinger"), "id")
    legg("handlinger[]", _mengde(hg.keys(), hn.keys()))
    for hid in set(hg) & set(hn):
        for klasse in _klassifiser_handling(hg[hid], hn[hid]):
            legg(f"handlinger[{hid}]", klasse)
        legg(f"handlinger[{hid}].grenser.tidsvindu", _tidsvindu_klasse(
            _hent(hg[hid], "grenser", "tidsvindu"),
            _hent(hn[hid], "grenser", "tidsvindu"),
            sone_gammel=sone_g, sone_ny=sone_n))

    # menneskelig_overstyring
    mg, mn = _hent(gammel, "menneskelig_overstyring"), _hent(ny, "menneskelig_overstyring")
    legg("menneskelig_overstyring", _mo_klasse(mg, mn))

    klasse = _samlet([e["klasse"] for e in endringer])
    resultat = {
        "klasse": klasse,
        "endringer": sorted(endringer, key=lambda e: e["sti"]),
        "klassifikatorversjon": KLASSIFIKATORVERSJON,
    }
    resultat["klassifisering_hash"] = hashlib.sha256(json.dumps(
        {"klasse": klasse, "endringer": resultat["endringer"],
         "v": KLASSIFIKATORVERSJON},
        sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return resultat


def _mo_klasse(gammel, ny) -> str:
    """menneskelig_overstyring: feltet lagt til / `godkjennbare` utvidet /
    `belop_maks` opp / `krever_fire_oyne` true→false / `krever_rolle` endret /
    `begrunnelse_pakrevd` true→false → UTVIDER; motsatt → INNSNEVRER."""
    if gammel == ny:
        return NØYTRAL
    if gammel is _FRAVÆR:                 # overstyring innført = mer fullmakt
        return UTVIDER
    if ny is _FRAVÆR:                     # overstyring fjernet = mindre fullmakt
        return INNSNEVRER
    if not isinstance(gammel, dict) or not isinstance(ny, dict):
        return UTVIDER
    delklasser = []
    # godkjennbare = mengde på (grunnkode, handling); i tillegg belop_maks per par
    gp = {(x.get("grunnkode"), x.get("handling")): x for x in _liste(gammel, "godkjennbare")}
    np = {(x.get("grunnkode"), x.get("handling")): x for x in _liste(ny, "godkjennbare")}
    delklasser.append(_mengde(gp.keys(), np.keys()))
    for k in set(gp) & set(np):
        delklasser.append(_skalar_opp(_hent(gp[k], "belop_maks"),
                                      _hent(np[k], "belop_maks")))
        delklasser.append(_valuta(_hent(gp[k], "valuta"), _hent(np[k], "valuta")))
    delklasser.append(_bool_ned(_hent(gammel, "krever_fire_oyne"),
                                _hent(ny, "krever_fire_oyne")))
    delklasser.append(_bool_ned(_hent(gammel, "begrunnelse_pakrevd"),
                                _hent(ny, "begrunnelse_pakrevd")))
    # krever_rolle: enhver endring er fail-closed UTVIDER (bredere rolle kan
    # slippe flere til; kan ikke ordnes uten rolle-hierarki i v1)
    if _hent(gammel, "krever_rolle") != _hent(ny, "krever_rolle"):
        delklasser.append(UTVIDER)
    return _samlet(delklasser)
