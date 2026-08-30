-- 071: skjul fra evalueringslisten (eiers funn 30/8)
--
-- «Oppdrag 94 og 97 har ikke slett-knapp — hvordan kan slettes?» Slett
-- betydde bare «slett lagrede kandidatdata» (069) — men en FEILET
-- evaluering uten anker og en REAPET (rapport utilgjengelig) har
-- ingenting å slette, og radene ble stående i listen for alltid. Det
-- eieren mener med slett er at RADEN forsvinner. Raden er historikk og
-- består i basen; dette merket lar listevisningen slippe den, og
-- slett-endepunktet setter det sammen med (ev.) tidligslettingen.
--
-- Kolonnelåsen under er 056-kroppen ORDRETT (SPEIL-presedensen: aldri
-- skriv naboens dør fra hukommelsen) pluss den nye enveis-armen.

ALTER TABLE oppdrag ADD COLUMN liste_skjult_ts TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION oppdrag_kolonnelaas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.unntak_id IS DISTINCT FROM OLD.unntak_id
       OR NEW.loggpost_id IS DISTINCT FROM OLD.loggpost_id
       OR NEW.repair_operation_id IS DISTINCT FROM OLD.repair_operation_id
       -- 056: opprinnelses-referansene er fødselsattributter, alle tre.
       OR NEW.beslutning_loggpost_id IS DISTINCT FROM OLD.beslutning_loggpost_id
       OR NEW.frigivelse_id IS DISTINCT FROM OLD.frigivelse_id
       OR NEW.oppdragstype IS DISTINCT FROM OLD.oppdragstype
       OR NEW.handling IS DISTINCT FROM OLD.handling
       OR NEW.eiermodul IS DISTINCT FROM OLD.eiermodul
       OR NEW.payload_kryptert IS DISTINCT FROM OLD.payload_kryptert
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.alg IS DISTINCT FROM OLD.alg
       OR NEW.nonce IS DISTINCT FROM OLD.nonce
       OR NEW.utforelsesfrist IS DISTINCT FROM OLD.utforelsesfrist
       OR NEW.evidensfrist IS DISTINCT FROM OLD.evidensfrist
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'oppdrag: kun status-, owner- og kvitteringsfelter kan endres';
    END IF;
    IF NEW.owner_generation < OLD.owner_generation THEN
        RAISE EXCEPTION 'oppdrag: owner_generation kan aldri reduseres';
    END IF;
    -- CP5 + Codex P1: kontraktbinding/epoch endres KUN av den herdede claim-
    -- funksjonen (eid av disponit_m37_claimer). Runtime har direkte UPDATE på
    -- oppdrag; uten denne current_user-sjekken kunne runtime initialisere en
    -- uclaimet rad med vilkårlig modul/versjon/hash (forfalske bindingen) eller
    -- nulle en stemplet epoch.
    IF current_user <> 'disponit_m37_claimer' AND (
           NEW.modul_id        IS DISTINCT FROM OLD.modul_id
        OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
        OR NEW.kontrakt_hash   IS DISTINCT FROM OLD.kontrakt_hash
        OR NEW.module_epoch    IS DISTINCT FROM OLD.module_epoch) THEN
        RAISE EXCEPTION 'oppdrag: kontraktbinding/epoch settes kun av claim-funksjonen';
    END IF;
    -- ... OG DEN TREDJE OPPRINNELSEN ER IKKE RUNTIMES RAD I DET HELE TATT
    -- (Codex P1, runde 12 på #140). `deploy/staging/migrer.py` gir
    -- runtime-rollen `SELECT, UPDATE ON oppdrag` (PR-006 → 038), og for de
    -- to ELDRE opprinnelsene er det riktig: runtime utfører dem selv, og
    -- statusmaskinen over er hele porten den trenger. Den tredje er en
    -- annen sak — autorisasjonen er en menneskelig signatur, utsendelsen
    -- er IRREVERSIBEL, og `oppdrag_en_per_frigivelse` (§5) gir frigivelsen
    -- NØYAKTIG ETT forsøk. Uten porten her kunne runtime, som med vilje
    -- ikke har EXECUTE på en eneste kjedefunksjon, likevel med rå UPDATE:
    --   * sette et ferskt frigivelsesoppdrag til `kansellert`/`feilet` —
    --     den autoriserte e-posten forsvinner, og raden kan aldri
    --     erstattes fordi frigivelsen alt har brukt sitt ene oppdrag;
    --   * kjøre det `opprettet`→`plukket`→`utfort` og fylle de initielt
    --     tomme kvitteringsfeltene selv — en utsending som ALDRI skjedde
    --     ser sendt ut, og kvitteringen er uforanderlig når den er satt.
    -- Begge er tap av selve påstanden kjeden finnes for, så porten dekker
    -- ALLE endringer på slike rader, ikke bare status og kvittering.
    -- Formen er en TILLATELSESLISTE, ikke en nektelsesliste: runtime-
    -- rollens NAVN er et installasjonsvalg (kjøreren tar det som
    -- argument), mens `disponit_m37_claimer` er husets faste eier av de to
    -- veiene som ER lovlige i dag — `claim_neste_oppdrag` (049) og
    -- `reap_evidensfrister` (§10), begge SECURITY DEFINER — og av
    -- kvitteringsveien CP3 må legge til. Å nekte «disponit» ved navn ville
    -- vært en port som forsvant på enhver installasjon med et annet navn.
    IF OLD.opprinnelse = 'frigivelse'
       AND current_user <> 'disponit_m37_claimer' THEN
        RAISE EXCEPTION 'oppdrag: et frigivelsesoppdrag endres kun av'
            ' kjedens egen eier (fikk %) — utsendelsen er irreversibel og'
            ' frigivelsen har nøyaktig ett forsøk', current_user
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Write-once (gjelder også claim-funksjonen på en reclaim): satt → frosset.
    IF OLD.modul_id IS NOT NULL AND (
           NEW.modul_id        IS DISTINCT FROM OLD.modul_id
        OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
        OR NEW.kontrakt_hash   IS DISTINCT FROM OLD.kontrakt_hash) THEN
        RAISE EXCEPTION 'oppdrag: kontraktbindingen er frosset når den er satt';
    END IF;
    -- module_epoch er monoton OG kan ikke nulles etter at den er satt (Codex P2:
    -- non-NULL→NULL fjernet fencing-generasjonen uten feil).
    IF OLD.module_epoch IS NOT NULL
       AND (NEW.module_epoch IS NULL OR NEW.module_epoch < OLD.module_epoch) THEN
        RAISE EXCEPTION 'oppdrag: module_epoch kan aldri reduseres/nulles';
    END IF;
    IF OLD.kvittering IS NOT NULL
       AND (NEW.kvittering IS DISTINCT FROM OLD.kvittering
            OR NEW.kvittering_signatur IS DISTINCT FROM OLD.kvittering_signatur
            OR NEW.resultathash IS DISTINCT FROM OLD.resultathash) THEN
        RAISE EXCEPTION 'oppdrag: kvitteringen er uforanderlig når den først er lagret';
    END IF;
    IF NOT (
        (OLD.status = 'opprettet' AND NEW.status IN ('plukket','kansellert','feilet')) OR
        (OLD.status = 'plukket'   AND NEW.status IN ('utfort','feilet','opprettet')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'oppdrag: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('utfort','feilet','kansellert') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'oppdrag: % er terminal', OLD.status;
    END IF;
    -- 071: LISTE-SKJULINGEN (eiers funn 30/8: «oppdrag 94 og 97 har
    -- ikke slett-knapp — hvordan kan slettes?»). Merket er ENVEIS og
    -- gjelder KUN terminale løp: settes én gang fra NULL, aldri frem i
    -- tid, aldri fjernet — og aldri på et løp som fortsatt kan bli noe
    -- (opprettet/plukket har Avbryt som sin vei). Raden består (den er
    -- historikk); det er LISTEVISNINGEN som slipper den.
    IF NEW.liste_skjult_ts IS DISTINCT FROM OLD.liste_skjult_ts THEN
        IF OLD.liste_skjult_ts IS NOT NULL THEN
            RAISE EXCEPTION 'oppdrag: liste_skjult_ts er enveis — satt er satt'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.liste_skjult_ts IS NULL
           OR NEW.liste_skjult_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'oppdrag: liste_skjult_ts settes til nå, aldri'
                ' frem i tid og aldri tilbake til NULL'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.status NOT IN ('utfort','feilet','kansellert') THEN
            RAISE EXCEPTION 'oppdrag: bare et terminalt løp kan skjules fra'
                ' listen — et aktivt har Avbryt som sin vei'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_ts := now();
    END IF;
    RETURN NEW;
END $$;
