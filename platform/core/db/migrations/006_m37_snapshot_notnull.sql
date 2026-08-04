-- ============================================================
-- Disponit migrasjon 006 — NOT NULL på policysnapshotet.
--
-- Egen versjon, og det er selve poenget: mellom 005 og 006 kjører
-- `db.m37_backfill.backfill()` fra `deploy/staging/migrer.py`. Den må
-- RE-HASHE lagret policyinnhold og sammenligne mot revisjonsloggen
-- (GO-vilkår V2), og den kanoniske hashen er definert i Python. En andre
-- implementasjon i PL/pgSQL ville vært duplikatformen som ga P1 nr. 4 i
-- PR-002.
--
-- Rekkefølgen er dermed en PORT, ikke en instruks: uteblir backfillen,
-- feiler denne migrasjonen på «column contains null values», og oppsettet
-- stopper. Det motsatte — nullable kolonner med et notat om at de bør
-- fylles — ville vært en advarsel med exit 0.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen. Kjøres av MIGRATOR.
-- ============================================================

-- Fail-closed sjekk FØR ALTER-en, med en melding som sier hva som mangler.
-- «column contains null values» forteller ikke hvem som skulle fylt den.
DO $$
DECLARE
    v_mangler BIGINT;
BEGIN
    SELECT count(*) INTO v_mangler
      FROM unntak
     WHERE maks_auto_forsok_snapshot IS NULL
        OR policy_versjon IS NULL
        OR policy_content_hash IS NULL;
    IF v_mangler > 0 THEN
        RAISE EXCEPTION
            '% unntaksrader mangler policysnapshot — kjør db.m37_backfill.backfill() FØR migrasjon 006 (deploy/staging/migrer.py gjør det automatisk)',
            v_mangler;
    END IF;
END $$;

ALTER TABLE unntak
    ALTER COLUMN maks_auto_forsok_snapshot SET NOT NULL,
    ALTER COLUMN policy_versjon            SET NOT NULL,
    ALTER COLUMN policy_content_hash       SET NOT NULL;

-- Nye saker skrives av API-veien med snapshotet på plass (api/kjerne.py).
-- Etter denne migrasjonen er «sak uten policykontekst» en tilstand
-- databasen ikke lenger kan representere.
