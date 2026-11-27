"""Saken koblet til det firmaet alt vet: kunden, fakturaene, tilbudene.

EIERS VEDTAK 27/11: «Bare kobling, ingen modell» — foreløpig. Å lese
e-post er ikke noe en kunde betaler for; det gjør Gmail og Outlook. Det
de IKKE vet, er hvem avsenderen er i firmaets egne registre og hva som
står åpent mot henne. Det er det denne lesingen svarer på, med FASTE
REGLER som kan forklares i én setning hver:

  * KUNDEN: avsenderadressen er registrert som kontakt på en kunde i
    partsregisteret (183). Gjenkjenningen går gjennom tenantens
    pseudonym — adressen sammenlignes aldri i klartekst i basen.
  * FAKTURA: fakturanummeret står i emnet eller teksten, ELLER fakturaen
    er kundens og står åpen.
  * TILBUD: tilbudet er til den samme kunden og er ikke forkastet.

Hver kobling bærer sin `grunn`, så flaten kan si HVORFOR — en kobling
man ikke kan etterprøve, er en gjetning.

INGENTING FORLATER HUSET, og ingenting lagres: lesingen regnes ut på
nytt hver gang, gjennom modulenes egne lesedører, og rulles tilbake.
Det som vises, følger brukerens EGNE scoper: kunden krever `part:read`,
fakturaer og tilbud `okonomi:read`. Å ha lov til å lese en e-post gir
ikke lov til å se kundens utestående.

Senere (eiers «1»): den samme koblingen er grunnlaget et svarforslag
skrives fra — da med de samme reglene, ikke nye.
"""
from __future__ import annotations

import re

#: Hvor mange fordringer/tilbud dørene spørres om. Dørene sorterer selv;
#: koblingen ser bare på de nyeste — en henvendelse om en faktura som
#: ligger utenfor dette vinduet, finnes ikke her, og det er ærlig.
VINDU = 500

#: Hvor mange koblinger av hver art som vises. En sak med 40 fakturaer
#: trenger de som står åpne, ikke en reskontro.
MAKS_VIST = 8

_ORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/]{1,39}")
_NOKKELORD = re.compile(
    r"(faktura|fakturanr|fakturanummer|invoice|inv\.?|nr\.?|#|kid)\W{0,3}$",
    re.IGNORECASE)


def kandidatnumre(tekst: str) -> set[str]:
    """Ord i teksten som KAN være et fakturanummer: minst tre tegn og
    minst ett siffer. Rene tall under fire siffer teller bare med et
    nøkkelord rett foran («faktura 104», «#104») — ellers ville «104 kr»
    og «kl 930» koblet saken til faktura 104 og 930."""
    ut: set[str] = set()
    for m in _ORD.finditer(tekst or ""):
        ord_ = m.group(0).strip("-/")
        if len(ord_) < 3 or not any(c.isdigit() for c in ord_):
            continue
        if ord_.isdigit() and len(ord_) < 4:
            foran = tekst[max(0, m.start() - 16):m.start()]
            if not _NOKKELORD.search(foran):
                continue
        ut.add(ord_.lower())
    return ut


def _rader(conn, sql: str, args) -> list[dict]:
    cur = conn.execute(sql, args)
    navn = [d.name for d in cur.description]
    return [dict(zip(navn, r)) for r in cur.fetchall()]


def _avsender(conn, tenant: str, kanal: str, ref: str | None) -> str | None:
    """Avsenderadressen for en e-postsak, fra meldingen den kom fra —
    dekryptert her, aldri lagret. None for andre kanaler, og for en
    melding som er slettet (da finnes adressen ikke lenger)."""
    if kanal != "epost" or not ref:
        return None
    from .epost_meldinger import _pakk_ut
    for ct, nonce, key_id in conn.execute(
            "SELECT kropp_kryptert, nonce, key_id FROM epost_melding"
            " WHERE tenant=%s AND leverandor_melding_id=%s"
            "   AND slettet_ts IS NULL"
            " ORDER BY mottatt_ts DESC NULLS LAST LIMIT 3",
            (tenant, ref)).fetchall():
        try:
            p = _pakk_ut(conn, tenant, (ct, nonce, key_id))
        except Exception:                                   # noqa: BLE001
            continue
        fra = str((p or {}).get("fra") or "").strip()
        if "@" in fra:
            return fra
    return None


def _tekst(conn, tenant: str, hid) -> str:
    """Emne + tekst i klartekst, gjennom M-17s egen innholdsdør."""
    from db import kryptering
    rad = conn.execute("SELECT * FROM m17_hent_innhold(%s,%s)",
                       (tenant, hid)).fetchone()
    if rad is None:
        return ""
    dek = kryptering.hent_dek(conn, tenant, rad[4])
    emne = kryptering.dekrypter(dek, rad[0], rad[1], tenant, rad[4])["t"]
    kropp = kryptering.dekrypter(dek, rad[2], rad[3], tenant, rad[4])["t"]
    return f"{emne}\n{kropp}"


def koblinger_for(conn, tenant: str, hid, scopes) -> dict | None:
    """-> {kunde, fakturaer, tilbud, sett} eller None for ukjent sak.

    `sett` sier hva som faktisk ble undersøkt — en tom liste fordi
    brukeren mangler scopet er ikke det samme som «ingen fakturaer»."""
    ref = conn.execute("SELECT * FROM m17_kilde_ref(%s,%s)",
                       (tenant, hid)).fetchone()
    if ref is None:
        return None
    kanal, ekstern_ref, _lukket = ref
    scopes = set(scopes or ())
    ser_kunde = "part:read" in scopes
    ser_okonomi = "okonomi:read" in scopes

    adresse = _avsender(conn, tenant, kanal, ekstern_ref)
    kunde = None
    if adresse and (ser_kunde or ser_okonomi):
        psn = conn.execute("SELECT tenant_pseudonym(%s,%s)",
                           (tenant, adresse)).fetchone()[0]
        p = conn.execute("SELECT * FROM part_fra_pseudonym(%s,%s)",
                         (tenant, psn)).fetchone()
        if p is not None:
            kunde = {"part_id": str(p[0]), "part_ref": p[1], "navn": p[2],
                     "grunn": "avsender"}

    fakturaer: list[dict] = []
    tilbud: list[dict] = []
    if ser_okonomi:
        numre = kandidatnumre(_tekst(conn, tenant, hid))
        for f in _rader(conn, "SELECT * FROM m23_fordringene(%s,%s)",
                        (tenant, VINDU)):
            nummer = str(f["fakturanummer"]).lower() in numre
            kundens = (kunde is not None and f.get("part_id") is not None
                       and str(f["part_id"]) == kunde["part_id"]
                       and f["status"] == "apen")
            if not (nummer or kundens):
                continue
            fakturaer.append({
                "fordring_id": str(f["fordring_id"]),
                "fakturanummer": f["fakturanummer"],
                "kunde": f.get("part_navn") or f["kunde_ref"],
                "rest_ore": f["rest_ore"], "belop_ore": f["belop_ore"],
                "forfall": f["forfall"].isoformat(),
                "dogn_over_forfall": f["dogn_over_forfall"],
                "status": f["status"], "trinn_navn": f["trinn_navn"],
                "grunn": "nummer" if nummer else "kunde"})
        # Nummeret i teksten FØRST — det er det kunden skriver om. Så de
        # eldste forfalte.
        fakturaer.sort(key=lambda x: (x["grunn"] != "nummer",
                                      -(x["dogn_over_forfall"] or 0)))
        if kunde is not None:
            for t in _rader(conn, "SELECT * FROM m26_tilbudene(%s,%s)",
                            (tenant, VINDU)):
                if t["status"] == "forkastet" or not t.get("kunde_ref") \
                        or t["kunde_ref"] != kunde["part_ref"]:
                    continue
                tilbud.append({
                    "tilbud_id": str(t["tilbud_id"]),
                    "kunde": t["kunde_navn"], "sum_ore": t["sum_ore"],
                    "valuta": t["valuta"], "status": t["status"],
                    "gyldig_til": t["gyldig_til"].isoformat(),
                    "grunn": "kunde"})
    if kunde is not None and not ser_kunde:
        # Fakturaene er funnet GJENNOM kunden, men navnet på kunden er
        # partsregisterets — uten `part:read` står bare at den finnes.
        kunde = {"grunn": "avsender", "skjult": True}
    return {"kunde": kunde,
            "fakturaer": fakturaer[:MAKS_VIST],
            "tilbud": tilbud[:MAKS_VIST],
            "flere_fakturaer": len(fakturaer) > MAKS_VIST,
            "sett": {"kunde": bool(adresse) and (ser_kunde or ser_okonomi),
                     "okonomi": ser_okonomi}}


def koblinger_endepunkt(tjeneste, request):
    """GET /v1/kundeservice/henvendelse/{henvendelse_id}/koblinger
    (kundeservice:innhold) — reglene leser hva kunden skrev, så scopet er
    innholdets. Hva som vises i tillegg, følger brukerens egne scoper."""
    from .kundeservice import _sti_uuid
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        hid = _sti_uuid(request, "henvendelse_id", rid)
        svar = koblinger_for(conn, auth.tenant, hid, auth.scopes)
        if svar is None:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("ikke_funnet", rid, 404))
        svar["henvendelse_id"] = str(hid)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "kundeservice:innhold", _fn)
