"""Seeder ÉN demo-rekrutteringsprosess gjennom den ekte kjeden — så eier
kan klikke bestilling → blindet evaluering → rangert liste → SIGNERE en
innstilt utsendelse i flaten på disponit.com.

Kjøres på serveren som migrator:

    DISPONIT_MIGRATOR_URL=... python deploy/staging/seed-rekruttering-demo.py \
        --epost <eiers-innloggingsepost> [--tenant T] [--kandidater 6]

Hva den gjør, i kjedens egen rekkefølge:
  1. finner eierens identitet (profil-epost) og tenant (aktivt medlemskap;
     flere → --tenant er påkrevd), og sørger for `admin`-rollen
     (bestilling:opprett — signeringsknappen) idempotent,
  2. loggpost → beslutningsoppdrag `rekruttering.evaluering` (m57_ats),
     `plukket` (fødselsporten: ankeret fødes MENS kjøringen står på),
  3. prosess + ALLE SEKS kandidatlagre per kandidat (blindede tekster med
     deterministisk demoinnhold; funn bærer kildereferanser inn i den
     blindede teksten; §5-fullstendighet er poenget, ikke pynt),
  4. `utfort`, og til slutt en innstilt invitasjonsliste gjennom
     `opprett_utsendingsliste` (056) — USIGNERT: signaturen er eierens
     klikk i flaten, det er selve demoen.

Demoen er LITEN (standard 6 kandidater) med vilje: full-last-sperrene
(#155/#163/#164/#165/#173) gjelder reelle bunter, ikke denne.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "..", "..", "platform/core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                "..", "..", "platform"))

VEKTER = {"drift": 3, "skytjenester": 2, "norsk": 1}

KANDIDATER = [
    ("Søker med full dekning", {"drift": True, "skytjenester": True,
                                "norsk": True}, []),
    ("Søker uten sky", {"drift": True, "skytjenester": False,
                        "norsk": True}, []),
    ("Søker med uklar tidslinje", {"drift": True, "skytjenester": True,
                                   "norsk": False},
     [("uklar_tidslinje", "arbeidet med drift i perioden")]),
    ("Søker uten dokumentert krav", {"drift": False,
                                     "skytjenester": False,
                                     "norsk": True},
     [("krav_ikke_dokumentert", "ingen drifts-erfaring nevnt")]),
    ("Søker med motstrid", {"drift": True, "skytjenester": True,
                            "norsk": True},
     [("motstridende_opplysning", "både 2019 og 2021 som startår")]),
    ("Søker utenfor frist", {"drift": True, "skytjenester": False,
                             "norsk": False},
     [("utenfor_soknadsfrist", "levert etter fristen")]),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--epost", required=True)
    p.add_argument("--tenant")
    p.add_argument("--kandidater", type=int, default=6)
    a = p.parse_args()

    from db.pg import koble, sett_kontekst
    url = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not url:
        print("AVBRUTT: DISPONIT_MIGRATOR_URL mangler", file=sys.stderr)
        return 2
    m = koble(url)

    rad = m.execute(
        "SELECT bruker_id FROM brukeridentitet"
        " WHERE profil->>'email' = %s OR profil->>'epost' = %s",
        (a.epost, a.epost)).fetchall()
    if len(rad) != 1:
        print(f"AVBRUTT: {len(rad)} identiteter for eposten — logg inn på"
              " portalen én gang først, eller presiser", file=sys.stderr)
        return 2
    bid = rad[0][0]
    medlemskap = m.execute(
        "SELECT tenant, roller FROM brukermedlemskap"
        " WHERE bruker_id=%s AND aktiv", (bid,)).fetchall()
    if a.tenant:
        medlemskap = [r for r in medlemskap if r[0] == a.tenant]
    if len(medlemskap) != 1:
        print(f"AVBRUTT: {len(medlemskap)} aktive medlemskap"
              f" ({[r[0] for r in medlemskap]}) — angi --tenant",
              file=sys.stderr)
        return 2
    tenant, roller = medlemskap[0]
    if "admin" not in roller:
        m.execute(
            "UPDATE brukermedlemskap SET roller = roller || '{admin}',"
            " authz_version = authz_version + 1"
            " WHERE bruker_id=%s AND tenant=%s", (bid, tenant))
        print(f"MERK: la til admin-rollen for signering i {tenant} —"
              " logg inn på nytt så økten ser den")
    sett_kontekst(m, tenant, "seed-demo", "seed-1")

    logg = m.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'seed-demo','api_token','demo','p@1.0.0/x.y',"
        " 'TILLAT','[]',%s) RETURNING id",
        (tenant, "m57-demo-" + uuid.uuid4().hex[:12])).fetchone()[0]
    from db import kryptering
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, tenant)
    ct, nonce = kryptering.krypter(
        dek, {"stillingsprofil_ref": "demo", "soknadsbunt_ref": "demo",
              "antall_soknader": a.kandidater, "omfang": "bunt"},
        tenant, key_id)
    oid = m.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, beslutning_loggpost_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " VALUES ('beslutning',%s,%s,'rekruttering.evaluering',"
        " 'rekruttering.evaluering.bunt','m57_ats',%s,%s,%s,"
        " now()+interval '4 hour', now()+interval '1 day','KOBLET')"
        " RETURNING id", (tenant, logg, ct, key_id, nonce)).fetchone()[0]
    m.execute("UPDATE oppdrag SET status='plukket' WHERE tenant=%s"
              " AND id=%s", (tenant, oid))
    m.commit()

    sett_kontekst(m, tenant, "seed-demo", "seed-2")
    m.execute("SET LOCAL ROLE disponit_m37_claimer")
    pid = m.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                    (tenant, oid)).fetchone()[0]

    antall_anbefalt = 0
    for n, (navn, oppfylt, funnliste) in enumerate(
            KANDIDATER[:a.kandidater]):
        kid, did = uuid.uuid4(), uuid.uuid4()
        blindet = (f"[NAVN-1] søker stillingen. Erfaring: "
                   f"{'drift, ' if oppfylt['drift'] else ''}"
                   f"{'skytjenester, ' if oppfylt['skytjenester'] else ''}"
                   "referanser på forespørsel. "
                   + " ".join(s for _, s in funnliste))
        funn = []
        for kategori, sitat in funnliste:
            start = blindet.index(sitat)
            funn.append({"kategori": kategori,
                         "kilde": {"start": start,
                                   "slutt": start + len(sitat),
                                   "sitat": sitat}})
        # TRAFIKKLYSET ER IKKE SEEDENS Å PÅSTÅ (Codex P1). Den kanoniske
        # artefakten `evaluering.evaluer_kandidat` returnerer har ingen
        # `status` i det hele tatt — flaten utleder den i
        # `api/rekruttering.py`. Seeden skrev feltet likevel, med en KOPI
        # av utledningen, og kopien bar nettopp feilen som ble rettet der:
        # «ingen funn» ble til «Anbefalt» uavhengig av oppfylte krav, så
        # «Søker uten sky» sto grønt i demoen. En demo som viser noe annet
        # enn produksjonsveien er ikke en demo. Feltet utelates nå, og
        # dermed er det ÉN utledning igjen — flatens egen.
        if not funn and all(oppfylt.values()):
            antall_anbefalt += 1
        # Evidensen regnes av NØYAKTIG de lagrede bytene (CodeRabbit
        # major): dokumentet bygges én gang, og både størrelse og hash
        # er avledet av det — aldri av en nabostreng pluss et påslag.
        dok = f"<p>{blindet}</p>".encode()
        sha = hashlib.sha256(dok).hexdigest()
        m.execute(
            "INSERT INTO kandidat_originaldokument (tenant, prosess_id,"
            " kandidat_id, dokument_id, filnavn, innholdstype, dokument,"
            " storrelse_bytes, innhold_sha256)"
            " VALUES (%s,%s,%s,%s,%s,'text/html',%s,%s,%s)",
            (tenant, pid, kid, did, f"soknad-{n+1}.html",
             dok, len(dok), sha))
        m.execute(
            "INSERT INTO kandidat_parsettekst (tenant, prosess_id,"
            " kandidat_id, dokument_id, tekst, innhold_sha256)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (tenant, pid, kid, did, blindet, sha))
        m.execute(
            "INSERT INTO kandidat_evalueringsartefakt (tenant, prosess_id,"
            " kandidat_id, artefakt, innhold_sha256)"
            " VALUES (%s,%s,%s,%s,%s)",
            (tenant, pid, kid, json.dumps({
                "oppfylt": oppfylt, "vekter": VEKTER, "funn": funn,
                "intervjusporsmal":
                    [f"Fortell mer om erfaringen din med "
                     f"{k}." for k, v in oppfylt.items() if v]}), sha))
        m.execute(
            "INSERT INTO kandidat_intervjusporsmal (tenant, prosess_id,"
            " kandidat_id, sporsmal, innhold_sha256)"
            " VALUES (%s,%s,%s,%s,%s)",
            (tenant, pid, kid,
             json.dumps([f"Spørsmål til {navn} (blindet i flaten)"]), sha))
        m.execute(
            "INSERT INTO kandidat_utsendingsdata (tenant, prosess_id,"
            " kandidat_id, mottaker_ref, flettefelt, innhold_sha256)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (tenant, pid, kid, f"demo-kandidat-{n+1}@example.invalid",
             json.dumps({"kandidatnavn": "[NAVN-1]",
                         "stilling": "Demo-stilling"}), sha))
        m.execute(
            "INSERT INTO kandidat_avmaskering (tenant, prosess_id,"
            " kandidat_id, felter, innhold_sha256)"
            " VALUES (%s,%s,%s,%s,%s)",
            (tenant, pid, kid,
             json.dumps({"[NAVN-1]": navn}), sha))
    m.commit()

    sett_kontekst(m, tenant, "seed-demo", "seed-3")
    m.execute("UPDATE oppdrag SET status='utfort' WHERE tenant=%s"
              " AND id=%s", (tenant, oid))
    m.commit()

    sett_kontekst(m, tenant, "seed-demo", "seed-4")
    m.execute("SET LOCAL ROLE disponit_m37_claimer")
    innhold_hash = hashlib.sha256(
        f"m57-demoliste:{pid}".encode()).hexdigest()
    lid = m.execute(
        "SELECT opprett_utsendingsliste(%s,%s,NULL,%s,'invitasjon',"
        "'invitasjon-v1',%s,%s)",
        (tenant, uuid.uuid4(), oid, innhold_hash,
         max(antall_anbefalt, 1))).fetchone()[0]
    m.commit()
    # SPA-SKALLET ER `/`, IKKE `/ui/` (Codex P2). `/ui/`-stien proxes
    # uendret (nginx `location /ui/`), og `ui_asset` slår opp på
    # FILENDELSEN: en tom `sti` har ingen, faller ut av `_CT` og svarer
    # 404. `/ui/` er assets-treet, `/` er skallet som laster ruteren —
    # og fragmentet går uansett aldri til serveren. Demo-anvisningen
    # endte altså på en feilside i stedet for på Rekruttering.
    print(f"SEEDET: tenant={tenant} prosess={pid} liste={lid}"
          f" innhold={innhold_hash[:12]}… — åpne /#/rekruttering og"
          " signer invitasjonen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
