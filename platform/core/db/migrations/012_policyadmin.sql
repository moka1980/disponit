-- ============================================================
-- 012 — Policyadministrasjon v1 (PR-013)
--
-- Å endre policy er å endre agentens fullmakter. Samme strenghet som
-- kodeutrulling: aktive versjoner uforanderlige · aktivering atomisk og
-- reversibel · ingen aktivering uten menneskelig godkjenning · ingen
-- godkjenning uten sett diff · utvidelse av fullmakt = egen risikoklasse.
--
-- Denne migrasjonen (CP1) legger DATAMODELLEN + integritetstriggere:
-- `policy_hode` (ankerrad + aktiv-peker), `policyutkast`, `aktiveringsrunde`,
-- `aktiveringsattestasjon`, kompositt-FK for pekeren, den avledede
-- `policyer.aktiv`-invarianten (deferrable constraint trigger), `er_forfatter`
-- server-utledning (trigger), status-statemaskiner og RLS. Den herdede
-- `aktiver_policy`-funksjonen, `DENY_ALL_V1` og policy-eier-rollen kommer i
-- aktiveringssteget (CP5), som `behandle_unntakshandling` kom etter 011s skjema.
--
-- SKJEMA-AVSTEMMING (implementer): `policyer.versjon` er TEXT (003, f.eks.
-- "1.0.0"). Spec v4 skriver `aktiv_versjon INT`. For at kompositt-FK-en
-- `(tenant,policy_id,aktiv_versjon) → policyer(tenant,policy_id,versjon)` skal
-- binde MÅ typene stemme → `aktiv_versjon` er TEXT. Monoton allokering skjer
-- via `neste_versjon INT` under `policy_hode`-låsen; den lagrede `versjon` er
-- `neste_versjon::text`. Dette bevarer spec-intensjonen (monoton, unik,
-- FK-bundet) uten å bryte den eksisterende TEXT-kolonnen.
-- ============================================================

-- ------------------------------------------------------------
-- 1. policy_hode — ankerrad, eneste aktiv-autoritet (V1, v4 §1, v5 §1)
--    ALLE aktiveringer (også første) låser denne FØRST. `aktiv_versjon` er
--    autoriteten motoren leser (NULL → DENY_ALL_V1). Kompositt-FK binder
--    pekeren til en faktisk policyrad; DEFERRABLE så aktivering kan sette inn
--    rad og flytte peker i samme transaksjon.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_hode (
    tenant        TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    policy_id     TEXT NOT NULL,
    neste_versjon INT  NOT NULL DEFAULT 1 CHECK (neste_versjon >= 1),
    aktiv_versjon TEXT,                        -- NULL = ingen aktiv (deny-all)
    revisjon      BIGINT NOT NULL DEFAULT 0,   -- monoton, +1 ved hver aktivering
    PRIMARY KEY (tenant, policy_id),
    CONSTRAINT policy_hode_peker_fk
        FOREIGN KEY (tenant, policy_id, aktiv_versjon)
        REFERENCES policyer (tenant, policy_id, versjon)
        DEFERRABLE INITIALLY DEFERRED
);

-- ------------------------------------------------------------
-- 2. policyutkast — eneste muterbare tilstand (spec §1–§2)
--    Et utkast er IKKE en policy; egen tabell. `utkastversjon` = optimistisk
--    lås ved redigering. `innholds_hash` settes ved validering og fryses.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policyutkast (
    tenant            TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    utkast_id         TEXT NOT NULL,
    policy_id         TEXT NOT NULL,
    basert_pa_versjon TEXT,                    -- aktiv versjon utkastet bygger på
    basert_pa_hash    TEXT,                    -- for konfliktdeteksjon (§4)
    rollback_av_versjon TEXT,                  -- satt når utkastet er en rullbakk
    innhold           JSONB NOT NULL,
    utkastversjon     INT  NOT NULL DEFAULT 1 CHECK (utkastversjon >= 1),
    status            TEXT NOT NULL DEFAULT 'utkast'
                      CHECK (status IN ('utkast','validert','godkjent',
                                        'forkastet','aktivert')),
    innholds_hash     TEXT,                    -- settes ved validering, så frosset
    opprettet_av      TEXT NOT NULL,
    opprettet         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, utkast_id)
);
CREATE INDEX IF NOT EXISTS policyutkast_policy
    ON policyutkast (tenant, policy_id, status);

-- ------------------------------------------------------------
-- 3. aktiveringsrunde — speiler godkjenningsrunde (PR-012). Binder ALT
--    klassifiseringen hviler på (v2 §2, v3 §4, v4 §2/§3, v5 §2): diff, klasse,
--    klassifikator-/skjema-/motorsemantikk-versjoner, deny-all, godkjennerkrav.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aktiveringsrunde (
    tenant                    TEXT NOT NULL,
    utkast_id                 TEXT NOT NULL,
    runde                     INT  NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'apen'
                              CHECK (status IN ('apen','klar','brukt',
                                                'utlopt','kansellert')),
    diff_hash                 TEXT NOT NULL,   -- hva godkjennerne SÅ (§4)
    utkast_innholds_hash      TEXT NOT NULL,
    base_policy_hash          TEXT NOT NULL,   -- aktiv hash (eller deny-all) da runden åpnet
    risikoklasse              TEXT NOT NULL
                              CHECK (risikoklasse IN ('UTVIDER','INNSNEVRER','NØYTRAL')),
    klassifisering_hash       TEXT NOT NULL,   -- SHA-256 JCS av klassifiseringsresultatet
    klassifikatorversjon      TEXT NOT NULL,
    policyskjema_versjon      TEXT NOT NULL,
    motor_semantikkversjon    TEXT NOT NULL,
    deny_all_hash             TEXT NOT NULL,
    deny_all_versjon          TEXT NOT NULL,
    pakrevd_antall_godkjennere INT NOT NULL CHECK (pakrevd_antall_godkjennere >= 1),
    decision_operation_id     TEXT,            -- settes ved klar→brukt (idempotens)
    apnet                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper                   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant, utkast_id, runde),
    FOREIGN KEY (tenant, utkast_id) REFERENCES policyutkast (tenant, utkast_id)
);
-- Én aktiv runde per utkast (som en_aktiv_runde i 011).
CREATE UNIQUE INDEX IF NOT EXISTS en_aktiv_aktiveringsrunde
    ON aktiveringsrunde (tenant, utkast_id) WHERE status IN ('apen','klar');
-- decision_operation_id er unik når satt (idempotent aktivering).
CREATE UNIQUE INDEX IF NOT EXISTS aktiveringsrunde_op
    ON aktiveringsrunde (tenant, decision_operation_id)
    WHERE decision_operation_id IS NOT NULL;

-- ------------------------------------------------------------
-- 4. aktiveringsattestasjon — speiler menneskelig_attestasjon (PR-012).
--    `diff_hash` MÅ matche rundens; `er_forfatter` er SERVER-UTLEDET (V7) og
--    håndheves av trigger nedenfor. Konvolutt `disponit_policy_activation_v1`.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aktiveringsattestasjon (
    id               BIGINT GENERATED ALWAYS AS IDENTITY,
    tenant           TEXT NOT NULL,
    utkast_id        TEXT NOT NULL,
    runde            INT  NOT NULL,
    bruker_id        TEXT NOT NULL,
    rolle            TEXT NOT NULL,
    authz_version    INT  NOT NULL,
    er_forfatter     BOOLEAN NOT NULL,         -- server-utledet (V7); trigger vokter
    diff_hash        TEXT NOT NULL,            -- MÅ matche rundens
    klassifisering_hash TEXT NOT NULL,
    risikoklasse     TEXT NOT NULL,
    konvoluttversjon INT  NOT NULL,
    konvolutt_hash   TEXT NOT NULL,            -- SHA-256 JCS
    mac              TEXT NOT NULL,
    mac_key_id       TEXT NOT NULL,
    jti              TEXT NOT NULL CHECK (length(jti) >= 22),
    utloper          TIMESTAMPTZ NOT NULL,
    saksversjon      INT,
    ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, id),
    UNIQUE (tenant, jti),                       -- engangsbruk
    UNIQUE (tenant, utkast_id, runde, bruker_id),
    FOREIGN KEY (tenant, utkast_id, runde)
        REFERENCES aktiveringsrunde (tenant, utkast_id, runde)
);

-- ------------------------------------------------------------
-- 5. Avledet `policyer.aktiv` ⇔ peker (V1, v5 §1). Deferrable constraint
--    triggere på BEGGE sider verifiserer ved COMMIT at, for hver (tenant,
--    policy_id): raden med aktiv=true er NØYAKTIG den `policy_hode.aktiv_versjon`
--    peker på — og at pekeren, når satt, treffer en aktiv rad. Deferred, så
--    aktiveringstransaksjonen kan sette inn rad + flytte peker før sjekken.
-- ------------------------------------------------------------
-- SECURITY DEFINER: triggeren fyrer i konteksten av den som skriver `policyer`
-- (også `disponit_m37_claimer` under `arkiver_policyversjon`), men må lese
-- `policy_hode`/`policyer`. Definer-modus gjør at den leser dem som eieren,
-- uten å gi skriverne tabelltilgang de ikke skal ha.
CREATE OR REPLACE FUNCTION policy_peker_konsistent()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_tenant   TEXT;
    v_policy   TEXT;
    v_peker    TEXT;
    v_aktiv    TEXT;      -- versjonen som faktisk har aktiv=true (om noen)
    v_antall   INT;
BEGIN
    -- Hvilken (tenant, policy_id) ble berørt.
    v_tenant := COALESCE(NEW.tenant, OLD.tenant);
    v_policy := COALESCE(NEW.policy_id, OLD.policy_id);

    -- Invarianten binder KUN policyadmin-styrte policyer (de som har en
    -- `policy_hode`-ankerrad). Eldre policyer registrert før PR-013 mangler
    -- hoderaden og er grandfathered til de eventuelt tas inn i policyadmin
    -- (da opprettes hoderaden, og fra da av håndheves invarianten).
    PERFORM 1 FROM public.policy_hode
     WHERE tenant = v_tenant AND policy_id = v_policy;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    SELECT aktiv_versjon INTO v_peker FROM public.policy_hode
     WHERE tenant = v_tenant AND policy_id = v_policy;

    SELECT count(*), max(versjon) INTO v_antall, v_aktiv
      FROM public.policyer
     WHERE tenant = v_tenant AND policy_id = v_policy AND aktiv;

    -- Høyst én aktiv rad (delindeksen garanterer det, men vi feiler tydelig).
    IF v_antall > 1 THEN
        RAISE EXCEPTION 'policyer: mer enn én aktiv versjon for %/%', v_tenant, v_policy;
    END IF;

    -- Peker NULL ⇔ ingen aktiv rad.
    IF v_peker IS NULL AND v_antall <> 0 THEN
        RAISE EXCEPTION 'policy_hode.aktiv_versjon er NULL men policyer har en aktiv rad (%/%)',
            v_tenant, v_policy;
    END IF;
    IF v_peker IS NOT NULL AND v_antall = 0 THEN
        RAISE EXCEPTION 'policy_hode peker på % men ingen aktiv rad finnes (%/%)',
            v_peker, v_tenant, v_policy;
    END IF;
    -- Peker satt ⇒ den aktive raden er NØYAKTIG den pekeren viser.
    IF v_peker IS NOT NULL AND v_aktiv IS DISTINCT FROM v_peker THEN
        RAISE EXCEPTION 'policyer.aktiv=% men policy_hode peker på % (%/%)',
            v_aktiv, v_peker, v_tenant, v_policy;
    END IF;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS policyer_peker_konsistent ON policyer;
CREATE CONSTRAINT TRIGGER policyer_peker_konsistent
    AFTER INSERT OR UPDATE OR DELETE ON policyer
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION policy_peker_konsistent();

DROP TRIGGER IF EXISTS policy_hode_peker_konsistent ON policy_hode;
CREATE CONSTRAINT TRIGGER policy_hode_peker_konsistent
    AFTER INSERT OR UPDATE ON policy_hode
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION policy_peker_konsistent();

-- ------------------------------------------------------------
-- 6. `er_forfatter` er server-utledet (V7). Triggeren avviser en attestasjon
--    der booleanen ikke stemmer med `bruker_id == policyutkast.opprettet_av`.
--    Fire-øyne-regelen avhenger dermed IKKE av at koden husker den — DB-en
--    håndhever den.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION attestasjon_er_forfatter_vokter()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    v_opprettet_av TEXT;
BEGIN
    SELECT u.opprettet_av INTO v_opprettet_av
      FROM public.policyutkast u
     WHERE u.tenant = NEW.tenant AND u.utkast_id = NEW.utkast_id;
    IF v_opprettet_av IS NULL THEN
        RAISE EXCEPTION 'aktiveringsattestasjon: ukjent utkast %', NEW.utkast_id;
    END IF;
    IF NEW.er_forfatter <> (NEW.bruker_id = v_opprettet_av) THEN
        RAISE EXCEPTION 'er_forfatter=% samsvarer ikke med identitetene (bruker=%, forfatter=%)',
            NEW.er_forfatter, NEW.bruker_id, v_opprettet_av;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS attestasjon_er_forfatter ON aktiveringsattestasjon;
CREATE TRIGGER attestasjon_er_forfatter
    BEFORE INSERT ON aktiveringsattestasjon
    FOR EACH ROW EXECUTE FUNCTION attestasjon_er_forfatter_vokter();

-- ------------------------------------------------------------
-- 7. Append-only + kolonnelås (frosne felt kan ikke endres i ettertid).
-- ------------------------------------------------------------
-- 7a. aktiveringsattestasjon er append-only (som menneskelig_attestasjon).
CREATE OR REPLACE FUNCTION aktiveringsattestasjon_append_only()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'aktiveringsattestasjon er append-only: % er forbudt', TG_OP;
END $$;
DROP TRIGGER IF EXISTS attestasjon_ingen_endring ON aktiveringsattestasjon;
CREATE TRIGGER attestasjon_ingen_endring
    BEFORE UPDATE OR DELETE ON aktiveringsattestasjon
    FOR EACH ROW EXECUTE FUNCTION aktiveringsattestasjon_append_only();

-- 7b. policyutkast: `innholds_hash` og `opprettet_av` fryses; status følger en
--     statemaskin; ved innholdsendring MÅ `utkastversjon` øke (optimistisk lås).
CREATE OR REPLACE FUNCTION policyutkast_kolonnelaas()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.utkast_id IS DISTINCT FROM OLD.utkast_id
       OR NEW.policy_id IS DISTINCT FROM OLD.policy_id THEN
        RAISE EXCEPTION 'policyutkast: identitet/opphav er uforanderlig';
    END IF;
    -- Frosset hash kan settes ÉN gang (NULL→verdi ved validering), aldri endres.
    IF OLD.innholds_hash IS NOT NULL
       AND NEW.innholds_hash IS DISTINCT FROM OLD.innholds_hash THEN
        RAISE EXCEPTION 'policyutkast: innholds_hash er frosset';
    END IF;
    -- Statusmaskin.
    IF NOT (
        (OLD.status = 'utkast'   AND NEW.status IN ('utkast','validert','forkastet')) OR
        (OLD.status = 'validert' AND NEW.status IN ('utkast','validert','godkjent','forkastet')) OR
        (OLD.status = 'godkjent' AND NEW.status IN ('validert','aktivert','forkastet')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'policyutkast: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;
    -- Terminaltilstander er uforanderlige.
    IF OLD.status IN ('forkastet','aktivert') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'policyutkast: % er terminal', OLD.status;
    END IF;
    -- Innholdsendring krever versjonsøkning (aldri tapt skriving).
    IF NEW.innhold IS DISTINCT FROM OLD.innhold
       AND NEW.utkastversjon <= OLD.utkastversjon THEN
        RAISE EXCEPTION 'policyutkast: innholdsendring krever høyere utkastversjon';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS utkast_kolonnelaas ON policyutkast;
CREATE TRIGGER utkast_kolonnelaas BEFORE UPDATE ON policyutkast
    FOR EACH ROW EXECUTE FUNCTION policyutkast_kolonnelaas();

-- 7c. aktiveringsrunde: alle bindingsfelt er frosne; kun status +
--     decision_operation_id kan endres, og status følger en statemaskin.
CREATE OR REPLACE FUNCTION aktiveringsrunde_kolonnelaas()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.diff_hash IS DISTINCT FROM OLD.diff_hash
       OR NEW.utkast_innholds_hash IS DISTINCT FROM OLD.utkast_innholds_hash
       OR NEW.base_policy_hash IS DISTINCT FROM OLD.base_policy_hash
       OR NEW.risikoklasse IS DISTINCT FROM OLD.risikoklasse
       OR NEW.klassifisering_hash IS DISTINCT FROM OLD.klassifisering_hash
       OR NEW.klassifikatorversjon IS DISTINCT FROM OLD.klassifikatorversjon
       OR NEW.policyskjema_versjon IS DISTINCT FROM OLD.policyskjema_versjon
       OR NEW.motor_semantikkversjon IS DISTINCT FROM OLD.motor_semantikkversjon
       OR NEW.deny_all_hash IS DISTINCT FROM OLD.deny_all_hash
       OR NEW.deny_all_versjon IS DISTINCT FROM OLD.deny_all_versjon
       OR NEW.pakrevd_antall_godkjennere IS DISTINCT FROM OLD.pakrevd_antall_godkjennere
       OR NEW.apnet IS DISTINCT FROM OLD.apnet THEN
        RAISE EXCEPTION 'aktiveringsrunde: bindingsfelt er uforanderlige';
    END IF;
    -- decision_operation_id settes ÉN gang (NULL→verdi ved klar→brukt).
    IF OLD.decision_operation_id IS NOT NULL
       AND NEW.decision_operation_id IS DISTINCT FROM OLD.decision_operation_id THEN
        RAISE EXCEPTION 'aktiveringsrunde: decision_operation_id er frosset';
    END IF;
    IF NOT (
        (OLD.status = 'apen' AND NEW.status IN ('apen','klar','utlopt','kansellert')) OR
        (OLD.status = 'klar' AND NEW.status IN ('brukt','utlopt','kansellert')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'aktiveringsrunde: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('brukt','utlopt','kansellert') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'aktiveringsrunde: % er terminal', OLD.status;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS runde_kolonnelaas ON aktiveringsrunde;
CREATE TRIGGER runde_kolonnelaas BEFORE UPDATE ON aktiveringsrunde
    FOR EACH ROW EXECUTE FUNCTION aktiveringsrunde_kolonnelaas();

-- ------------------------------------------------------------
-- 8. Ingen sletting av policy_hode (ankerrad består så lenge tenant finnes).
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION policy_hode_ingen_sletting()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'policy_hode kan ikke slettes (%/%)', OLD.tenant, OLD.policy_id;
END $$;
DROP TRIGGER IF EXISTS hode_ingen_sletting ON policy_hode;
CREATE TRIGGER hode_ingen_sletting BEFORE DELETE ON policy_hode
    FOR EACH ROW EXECUTE FUNCTION policy_hode_ingen_sletting();

-- ------------------------------------------------------------
-- 9. RLS + FORCE + tenant-isolasjon (mønster fra 003/011).
-- ------------------------------------------------------------
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['policy_hode','policyutkast','aktiveringsrunde',
                             'aktiveringsattestasjon']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolasjon ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolasjon ON %I
                USING      (tenant = current_setting(''disponit.tenant'', true))
                WITH CHECK (tenant = current_setting(''disponit.tenant'', true))', t);
    END LOOP;
END $$;
