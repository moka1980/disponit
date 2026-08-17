#!/usr/bin/env python3
"""Token-administrasjon (PR-005b korreksjon 2). Kjøres på serveren.

Kjører som `disponit_token_admin`: minimal DML på `api_tokener`, INSERT i
revisjonsloggen, og eier ingenting. Rollen kan verken lese `secret_mac`
(kolonnenivå-GRANT) eller kalle `verifiser_token` — den administrerer
tokens, den bruker dem ikke.

BRUK:
  DISPONIT_TOKEN_ADMIN_URL=... DISPONIT_TOKEN_PEPPER=... \\
    python3 deploy/staging/token-cli.py opprett --tenant t1 --rolle agent \\
      --scope decision:write --scope exceptions:read
  ... token-cli.py roter <token_id>
  ... token-cli.py deaktiver <token_id>
  ... token-cli.py list [--tenant t1]

HEMMELIGHETEN VISES ÉN GANG, og kun på et interaktivt terminalvindu.
Den kan ikke oppgis som argument, finnes ikke i shell-historikken, og
lagres aldri i klartekst noe sted — databasen har kun HMAC(pepper, secret),
og pepperet ligger hos API-prosessen.
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys

HANDLINGER = {"opprett": "token.opprett", "roter": "token.roter",
              "deaktiver": "token.deaktiver"}


def _mac(pepper: str, secret: str) -> str:
    return hmac.new(pepper.encode("utf-8"), secret.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def krev_miljo() -> tuple[str, str]:
    dsn = os.environ.get("DISPONIT_TOKEN_ADMIN_URL")
    pepper = os.environ.get("DISPONIT_TOKEN_PEPPER", "")
    if not dsn:
        raise SystemExit("AVBRUTT: DISPONIT_TOKEN_ADMIN_URL mangler")
    if len(pepper) < 32:
        raise SystemExit("AVBRUTT: DISPONIT_TOKEN_PEPPER mangler eller er"
                         " kortere enn 32 tegn")
    return dsn, pepper


def vis_hemmelighet(token_id: str, secret: str, bootstrap: bool,
                    ut=None) -> bool:
    """Én visning, kun på TTY — med mindre --bootstrap er gitt.

    Uten TTY-kravet havner hemmeligheten i den filen noen tilfeldigvis
    omdirigerte stdout til, eller i CI-loggen. `--bootstrap` er den
    eksplisitte, loggede unntaksveien for førstegangsoppsett der det ikke
    FINNES en terminal — den skal være et bevisst valg, ikke standarden.

    -> True hvis hemmeligheten faktisk ble levert. PR-009: tokenet er
    PENDING til dette har skjedd — en hemmelighet ingen har sett skal
    aldri bli et aktivt token.
    """
    ut = ut or sys.stdout
    interaktiv = bool(getattr(ut, "isatty", lambda: False)())
    if not interaktiv and not bootstrap:
        print(f"token_id: {token_id}", file=ut)
        print("HEMMELIGHETEN BLE IKKE VIST: stdout er ikke en terminal."
              " Kjør på nytt fra et terminalvindu, eller bruk --bootstrap"
              " hvis dette er et maskinelt førstegangsoppsett.", file=ut)
        print("Tokenet forblir PENDING og tilbakekalles nå — lag et nytt.",
              file=ut)
        return False
    print(f"\n  {token_id}.{secret}\n", file=ut)
    print("Vises kun denne ene gangen. Databasen har bare HMAC-en.", file=ut)
    return True


def _logg(conn, tenant: str, handling: str, detalj: dict) -> None:
    """Revisjonsloggpost for tokenhandlingen — SAMME transaksjon som endringen.

    Uten «samme transaksjon» kan et token bli opprettet uten spor, eller et
    spor bli skrevet for noe som aldri skjedde. `disponit.aktor` settes
    fordi triggere og RLS krever kontekst; `disponit.tenant` fordi
    revisjonsloggen har row level security med FORCE.
    """
    conn.execute("SELECT set_config('disponit.tenant', %s, true),"
                 "       set_config('disponit.aktor', 'token-cli', true)",
                 (tenant,))
    conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, handling)"
        " VALUES (%s,'token-cli','cli',%s,'token-cli','TILLAT',%s,%s)",
        (tenant,
         hashlib.sha256(json.dumps(detalj, sort_keys=True).encode()).hexdigest(),
         json.dumps([{"kode": handling, "params": detalj}], ensure_ascii=False),
         handling))


def opprett(conn, pepper: str, tenant: str, rolle: str, scopes: list[str],
            utloper=None) -> tuple[str, str]:
    """-> (token_id, secret). Én transaksjon: token + revisjonsloggpost.

    PR-009: raden fødes som PENDING (kolonnens default) og kan ikke
    autentisere. Aktivering er et EGET, eksplisitt steg som først skjer
    når hemmeligheten beviselig er levert (v4 §1).
    """
    # 035, port 24 (deploy-port): CLI-en kan ALDRI utstede et claim-dyktig
    # token. Eiermodulers claim-fullmakt (`orders:execute:*`) kommer KUN fra
    # modul-onboarding (engangshemmelighet → modultoken, bundet til
    # deployment/release/epoch) — et api-token med ordre-scopes ville vært
    # nettopp den ubundne, spoofbare identiteten onboardingen fjerner.
    ordre = sorted(sc for sc in scopes if sc.startswith("orders:execute"))
    if ordre:
        raise SystemExit(
            "AVBRUTT: orders:execute-scopes utstedes aldri herfra — bruk"
            f" modul-onboarding (035). Avvist: {', '.join(ordre)}")
    token_id = "tk_" + secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)          # >= 256 bits CSPRNG
    conn.execute(
        "INSERT INTO api_tokener (token_id, tenant, rolle, scopes, secret_mac,"
        " utloper) VALUES (%s,%s,%s,%s,%s,%s)",
        (token_id, tenant, rolle, scopes, _mac(pepper, secret), utloper))
    _logg(conn, tenant, HANDLINGER["opprett"],
          {"token_id": token_id, "rolle": rolle, "scopes": sorted(scopes)})
    return token_id, secret


def verifiser_pending(conn, pepper: str, token_id: str, secret: str) -> None:
    """Lokal verifisering (klarsignalets V2): hemmeligheten i minnet matcher
    lagret MAC — beregnet HER med pepperet, sammenlignet konstant-tid.
    Databasen ser aldri pepperet; `hent_pending_token` gir kun metadata og
    MAC for et PENDING-token, og gjør det aldri til en API-principal."""
    rad = conn.execute("SELECT secret_mac FROM hent_pending_token(%s)",
                       (token_id,)).fetchone()
    if rad is None:
        raise SystemExit(f"AVBRUTT: {token_id!r} er ikke PENDING — kan ikke"
                         " verifiseres")
    if not hmac.compare_digest(rad[0], _mac(pepper, secret)):
        raise SystemExit("AVBRUTT: lagret MAC matcher ikke hemmeligheten —"
                         " tokenet tilbakekalles")


def aktiver(conn, token_id: str) -> str:
    """PENDING -> AKTIV, atomisk. -> tenant."""
    rad = conn.execute(
        "UPDATE api_tokener SET status='AKTIV'"
        " WHERE token_id=%s AND status='PENDING' RETURNING tenant",
        (token_id,)).fetchone()
    if rad is None:
        raise SystemExit(f"AVBRUTT: {token_id!r} er ikke PENDING — ingen"
                         " aktivering")
    _logg(conn, rad[0], "token.aktiver", {"token_id": token_id})
    return rad[0]


def deaktiver(conn, token_id: str) -> str:
    """-> tenant. `status` er eneste autoritet (v5 §1) — tilbakekalling er
    en statusovergang, og den sperrer umiddelbart uansett utgangspunkt
    (PENDING eller AKTIV)."""
    rad = conn.execute(
        "UPDATE api_tokener SET status='TILBAKEKALT'"
        " WHERE token_id=%s AND status <> 'TILBAKEKALT' RETURNING tenant",
        (token_id,)).fetchone()
    if rad is None:
        raise SystemExit(f"AVBRUTT: ukjent eller allerede tilbakekalt token"
                         f" {token_id!r}")
    _logg(conn, rad[0], HANDLINGER["deaktiver"], {"token_id": token_id})
    return rad[0]


def roter(conn, pepper: str, gammel_id: str) -> tuple[str, str, str]:
    """NY FØRST, så deaktiver den gamle — i to transaksjoner.

    Rekkefølgen er kontrakten (korreksjon 2): feiler noe underveis, står
    kunden igjen med et token som virker. Motsatt rekkefølge — eller begge
    i én transaksjon der den nye hemmeligheten skrives over den gamle —
    gir et vindu, eller en tilstand, der ingen av dem virker og kunden er
    låst ute uten mulighet til å be om et nytt.

    PR-009: den nye fødes PENDING og aktiveres av kalleren ETTER levert
    hemmelighet — den gamle deaktiveres først når den nye ER aktiv, ellers
    står kunden tokenløs i vinduet.
    """
    rad = conn.execute(
        "SELECT tenant, rolle, scopes, utloper FROM api_tokener"
        " WHERE token_id=%s AND status='AKTIV'", (gammel_id,)).fetchone()
    if rad is None:
        raise SystemExit(f"AVBRUTT: ukjent eller inaktivt token {gammel_id!r}")
    tenant, rolle, scopes, utloper = rad
    ny_id, secret = opprett(conn, pepper, tenant, rolle, list(scopes), utloper)
    _logg(conn, tenant, HANDLINGER["roter"],
          {"fra": gammel_id, "til": ny_id})
    conn.commit()                       # TRANSAKSJON 1: den nye finnes (PENDING)
    return ny_id, secret, tenant


def _seremoni_ny(conn, pepper: str, bootstrap: bool, lag) -> tuple[str, str]:
    """v4 §1-sekvensen for et nytt token, felles for opprett og roter:

      TTY bekreftes FØR noe genereres → PENDING opprettes → LOKAL
      verifisering (MAC via `hent_pending_token`, pepper kun i minnet) →
      visning → operatørbekreftelse → atomisk aktivering.

    Enhver feil underveis tilbakekaller PENDING-tokenet — det finnes ingen
    vei til et aktivt token ingen holder hemmeligheten til. `--bootstrap`
    er den eksplisitte maskinveien (ingen TTY, ingen bekreftelse); den
    LEVERER hemmeligheten på stdout og aktiverer, og er valgt inn, aldri
    standard.
    """
    if not bootstrap and not sys.stdout.isatty():
        # FØR generering (v4 §1.1): ingen hemmelighet produseres hvis den
        # ikke kan leveres.
        raise SystemExit("AVBRUTT: stdout er ikke en terminal — kjør fra et"
                         " terminalvindu, eller bruk --bootstrap for et"
                         " maskinelt førstegangsoppsett")
    token_id, secret = lag()
    conn.commit()                       # PENDING finnes — kan ikke autentisere
    try:
        verifiser_pending(conn, pepper, token_id, secret)
        if not vis_hemmelighet(token_id, secret, bootstrap):
            raise SystemExit("AVBRUTT: hemmeligheten ble ikke levert")
        if not bootstrap:
            svar = input("Bekreft at hemmeligheten er lagret [ja/N]: ")
            if svar.strip().lower() != "ja":
                raise SystemExit("AVBRUTT: ikke bekreftet — tokenet"
                                 " tilbakekalles")
        aktiver(conn, token_id)
        conn.commit()
        return token_id, secret
    except BaseException:
        conn.rollback()
        deaktiver(conn, token_id)
        conn.commit()
        raise


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    under = p.add_subparsers(dest="kommando", required=True)

    o = under.add_parser("opprett")
    o.add_argument("--tenant", required=True)
    o.add_argument("--rolle", required=True)
    o.add_argument("--scope", action="append", default=[], dest="scopes")
    o.add_argument("--bootstrap", action="store_true",
                   help="tillat visning uten TTY (maskinelt førstegangsoppsett)")

    r = under.add_parser("roter")
    r.add_argument("token_id")
    r.add_argument("--bootstrap", action="store_true")

    d = under.add_parser("deaktiver")
    d.add_argument("token_id")

    l = under.add_parser("list")
    l.add_argument("--tenant")

    # PR-009 V3: opprydding av foreldede PENDING-rader — kjøres av
    # systemd-timeren, og korrektheten avhenger ALDRI av at en
    # signalhandler i den interaktive flyten rakk å rydde selv.
    ry = under.add_parser("rydd-pending")
    ry.add_argument("--ttl-minutter", type=int, default=30)

    # Ingen `--secret`. Hemmeligheter tas aldri fra kommandolinjen: de blir
    # stående i shell-historikken, i `ps`-utskriften og i `set -x`-loggen.
    args = p.parse_args(argv)

    sys.path.insert(0, str(__import__("pathlib").Path(__file__)
                           .resolve().parents[2] / "platform/core"))
    # PR-009: systemd-credentials (LoadCredential) hydreres før env-lesing —
    # rydd-pending-timeren kjører uten EnvironmentFile-hemmeligheter.
    from db.hemmeligheter import last_credentials
    last_credentials()
    dsn, pepper = krev_miljo()
    from db.pg import koble

    conn = koble(dsn)
    try:
        if args.kommando == "opprett":
            if not args.scopes:
                raise SystemExit("AVBRUTT: minst ett --scope kreves")
            token_id, secret = _seremoni_ny(conn, pepper, args.bootstrap,
                                            lambda: opprett(
                                                conn, pepper, args.tenant,
                                                args.rolle, args.scopes))
            print(f"aktivert: {token_id}")
        elif args.kommando == "roter":
            boks: dict = {}

            def _lag():
                ny_id, secret, tenant = roter(conn, pepper, args.token_id)
                boks["gammel"] = args.token_id
                return ny_id, secret
            ny_id, _ = _seremoni_ny(conn, pepper, args.bootstrap, _lag)
            # Gammel deaktiveres FØRST NÅR den nye er aktiv — ellers står
            # kunden tokenløs hvis noe over feilet (korreksjon 2).
            deaktiver(conn, boks["gammel"])
            conn.commit()
            print(f"aktivert: {ny_id} · tilbakekalt: {boks['gammel']}")
        elif args.kommando == "deaktiver":
            tenant = deaktiver(conn, args.token_id)
            conn.commit()
            print(f"tilbakekalt: {args.token_id} (tenant {tenant})")
        elif args.kommando == "rydd-pending":
            rader = conn.execute(
                "UPDATE api_tokener SET status='TILBAKEKALT'"
                " WHERE status='PENDING'"
                "   AND opprettet < now() - make_interval(mins => %s)"
                " RETURNING token_id, tenant", (args.ttl_minutter,)).fetchall()
            for tid, tenant in rader:
                _logg(conn, tenant, "token.pending_utlopt", {"token_id": tid})
            conn.commit()
            print(f"ryddet: {len(rader)} foreldede PENDING-tokens")
        elif args.kommando == "list":
            sql = ("SELECT token_id, tenant, rolle, scopes, status, utloper,"
                   " last_used_at FROM api_tokener")
            arg = ()
            if args.tenant:
                sql += " WHERE tenant=%s"
                arg = (args.tenant,)
            sql += " ORDER BY opprettet"
            for rad in conn.execute(sql, arg).fetchall():
                print("\t".join(str(x) for x in rad))
            conn.rollback()
    except BaseException:
        # Hemmeligheten finnes som lokal variabel i rammene over. Den skal
        # ikke havne i en traceback som kan bli logget — derfor skrives kun
        # feiltypen ut, aldri stacken.
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"AVBRUTT: {type(e).__name__}", file=sys.stderr)
        sys.exit(1)
