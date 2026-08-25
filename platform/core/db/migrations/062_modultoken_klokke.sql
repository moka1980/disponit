-- 062: modultoken-predikatets klokke (#205) — veggklokke, ikke
-- transaksjonsfrossen now().
--
-- `modultoken_fortsatt_autorisert` tar først delt advisory-lås på
-- modulen og måler DERETTER tokenets tidsfelter. Ventet kallet bak en
-- eksklusiv holder — altså under et nødstopp eller en rotasjon, som er
-- hendelsene porten finnes for — var `now()` fortsatt transaksjonens
-- STARTTID: et token som utløp mens vi ventet, fikk 'ok'. Samme
-- defektklasse som alt er lukket for claim-leasen (060:101, som bruker
-- clock_timestamp()) og for oppdrag-siden i #140 runde 9;
-- tokenhalvdelen sto igjen (Codex P2 på #202, 3854730233; dømt til
-- egen herding i #202-tråden — denne).
--
-- `modulhode`-leddene (status/epoch) er VURDERT og står på now()-frie
-- sammenligninger allerede: ingen av dem er tidsbaserte, så de har
-- ingen klokke å lese feil.
--
-- Kroppen er 035 ORDRETT — eneste endring er klokka i de to
-- tidsleddene (SPEIL-presedensen: aldri skriv naboens dør fra
-- hukommelsen).

SET LOCAL ROLE disponit_modul_eier;

CREATE OR REPLACE FUNCTION modultoken_fortsatt_autorisert(
    p_token_id UUID, p_modul_id TEXT, p_miljo TEXT, p_release_id TEXT,
    p_utstedt_epoch BIGINT)
RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_epoch BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock_shared(
        hashtextextended('modul:' || p_modul_id, 0));
    -- MODULEN FØRST, deretter tokenet — samme rekkefølge som claim-portens
    -- (`_modulporten` i app-laget), og det er ikke en stilsak: nødstoppet
    -- gjør BEGGE deler i én transaksjon (epoch++ og tilbakekalling av hele
    -- familien). Leste vi tokenet først, ville nettopp nødstoppet — det
    -- denne porten finnes for — svart «tokenet ditt er ugyldig» der claim
    -- svarer «modulen er stoppet», om den samme hendelsen. Deploymenten
    -- skal få den samme dommen uansett hvilken dør den står i.
    SELECT h.status, h.module_epoch INTO v_status, v_epoch
      FROM public.modulhode h WHERE h.modul_id = p_modul_id;
    -- Fra `aktiv` er `nodeaktivert` den eneste utgangen (statusmaskinen i
    -- 014), så en modul som ikke lenger er aktiv når kapabiliteten skal
    -- innløses, ER stoppet — ikke midlertidig et annet sted i livsløpet.
    IF NOT FOUND OR v_status <> 'aktiv' THEN
        RETURN 'modul_ikke_claimbar';
    END IF;
    IF v_epoch IS DISTINCT FROM p_utstedt_epoch THEN
        RETURN 'modulepoch_utdatert';
    END IF;
    -- Tokenet selv: tilbakekalt (manuell tilbakekalling av NETTOPP dette
    -- tokenet, eller en rotasjonsnåde som løp ut mens requesten pågikk)
    -- eller utløpt => ingen autentisert deployment lenger. Identiteten må
    -- dessuten fortsatt være DEN requesten ble autentisert som — et token
    -- kan ikke flytte seg mellom deployments, men porten sammenligner
    -- heller enn å anta det.
    --
    -- VEGGKLOKKEN, ikke now() (#205): porten kan ha VENTET bak den
    -- eksklusive modul-låsen over — nødstoppet/rotasjonen er nettopp
    -- når den venter — og transaksjonsfrossen now() ville målt tiden
    -- FØR ventingen. Et token som utløp mens vi sto i kø, skal dømmes
    -- som utløpt.
    PERFORM 1 FROM public.modultoken t
     WHERE t.token_id = p_token_id
       AND t.modul_id = p_modul_id
       AND t.miljo IS NOT DISTINCT FROM p_miljo
       AND t.release_id IS NOT DISTINCT FROM p_release_id
       AND (t.tilbakekalt_ts IS NULL
            OR t.tilbakekalt_ts > clock_timestamp())
       AND t.utloper > clock_timestamp();
    IF NOT FOUND THEN
        RETURN 'token_ugyldig';
    END IF;
    RETURN 'ok';
END $$;

RESET ROLE;
