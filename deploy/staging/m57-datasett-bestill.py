#!/usr/bin/env python3
"""M-57 datasettpunktet: bunten er golden v2 EKSAKT — hver av de 24
tekstene ti ganger (240 søknader), uten navn i teksten og uten hilsen, så
modellen ser nøyaktig bytene ankeret ble målt på (31/8). Kjøres på verten
som root; tokenet leses fra en root-eid fil og printes aldri. Skriver
oppdrag-id, beslutning og buntens sha256."""
import hashlib, html, io, json, os, secrets, sys, urllib.request, urllib.error, zipfile
from pathlib import Path
GJENTAK = int(sys.argv[1]) if len(sys.argv) > 1 else 10
API = "https://disponit.com"
PROFIL = "1f5a201c-e602-4c1d-bb08-8ad4ade2518a@1"
tok = Path(os.environ.get("DISPONIT_BESTILLERTOKENFIL", "/root/m57-datasett.token")).read_text(encoding="utf-8").strip()
gfil = Path("deploy/staging/m57-golden-v2.json")
golden = json.loads(gfil.read_text(encoding="utf-8"))
buf = io.BytesIO(); soknader = []
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    i = 0
    for r in range(GJENTAK):
        for g in golden:
            i += 1
            kid = f"d-{i:04d}"; navn = f"Kandidat{i:04d}"
            # PRODUKSJONSFORMEN: navnet står i teksten (blindingen krever
            # at hvert deklarert felt finnes) og maskeres til [NAVN-1].
            tekst = g["tekst"] + f"\n\nMed vennlig hilsen {navn}"
            z.writestr(f"{kid}/soknad.html",
                       "<html><body><p>" + html.escape(tekst).replace("\n", "<br>") + "</p></body></html>")
            soknader.append({"kandidat_id": kid, "filer": [f"{kid}/soknad.html"],
                             "felter": {"navn": [navn]}})
    z.writestr("soknader.json", json.dumps({"soknader": soknader}))
kropp = buf.getvalue()
N = len(soknader)
print("golden_sha256:", hashlib.sha256(gfil.read_bytes()).hexdigest())
print("bunt_sha256:", hashlib.sha256(kropp).hexdigest(), "soknader:", N)
def kall(metode, sti, data=None, ctype="application/json"):
    req = urllib.request.Request(API + sti, data=data, method=metode)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Idempotency-Key", "datasett-" + secrets.token_hex(8))
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
st, sv = kall("POST", "/v1/inndata/reserver", json.dumps({"eiermodul": "m57_ats", "formaal": "soknadsbunt"}).encode())
print("reserver:", st, sv if st != 201 else "ok")
if st != 201: sys.exit(1)
jti, ref = sv["reservasjon_jti"], sv["inndata_ref"]
st, sv = kall("PUT", f"/v1/inndata/opplast/{jti}", kropp, "application/zip")
print("opplast:", st, f"{len(kropp)} byte", sv if st != 201 else "ok")
if st != 201: sys.exit(1)
st, sv = kall("POST", "/v1/bestilling", json.dumps({"bestillingstype": "rekruttering.evaluering", "inndata_ref": ref,
              "stillingsprofil_ref": PROFIL, "antall_soknader": N, "omfang": "bunt"}).encode())
print("bestilling:", st, {k: sv.get(k) for k in ("beslutning", "oppdrag_id", "kode", "feil")} if isinstance(sv, dict) else sv)
