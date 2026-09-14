-- 201: KUNDEN KAN SI OPP SITT EGET ABONNEMENT.
--
-- Eier: «kunden kan avbryte prøveperioden og samme etter 30 dager ikke
-- fortsette.»
--
-- MÅLT FØR DETTE: `firma_sett_status` er grantet til MIGRATOR ALENE. En
-- kunde kunne altså ikke si opp uten å be eieren gjøre det for seg — og et
-- abonnement du ikke kommer ut av selv, er ikke en prøveperiode, det er en
-- binding.
--
-- HVORFOR IKKE BARE GRANTE `firma_sett_status` TIL RUNTIME
-- Fordi den tar status som PARAMETER. Med den i hånden kunne et firma satt
-- seg selv `aktiv` — altså gitt seg selv et betalt abonnement gratis. Det
-- funnet er alt gjort én gang (CodeRabbit, 190) og grantet ble fjernet. Å
-- gjeninnføre det her ville vært å reise den samme feilen igjen, bare med
-- en penere begrunnelse.
--
-- DØRA GÅR ÉN VEI, OG DET ER HELE POENGET
-- `stengt` er det eneste utfallet. Kunden eier retten til å GÅ; hun eier
-- ikke retten til å komme tilbake til en tilstand hun ikke har betalt for.
-- 190s overgangstabell sier at `stengt → aktiv` er den eneste veien ut av
-- `stengt` — så en «angre»-knapp her ville i praksis vært en
-- oppgraderingsknapp. Gjenåpning er derfor plattformeierens handling
-- (`plattform_firma_status`, 199), og angrefristen i 190 gjør at det
-- fortsatt er mulig: raden og dataene står til `slettefrist_dogn` er ute.
--
-- ARBEIDET GJØRES AV 190s EGEN DØR. `firma_sett_status` håndhever
-- overgangstabellen, angrefristen og `stengt_ts`/`stengt_av`; denne låser
-- bare fast HVILKEN overgang som er lov herfra. En kopi av statusmaskinen
-- ville drevet fra originalen.
-- ============================================================

CREATE OR REPLACE FUNCTION firma_kunde_avslutt(p_tenant TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_naa TEXT;
BEGIN
    -- 038s form: tenanten bindes til KONTEKSTEN, aldri til parameteret
    -- alene. Uten dette kunne en kunde sagt opp et annet firmas abonnement
    -- ved å sende dets navn.
    PERFORM public.krev_tenantkontekst(p_tenant, 'firma_kunde_avslutt');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'firma_kunde_avslutt: en oppsigelse har en aktør'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT status INTO v_naa FROM public.firma
     WHERE firma.tenant = p_tenant FOR UPDATE;
    IF v_naa IS NULL THEN
        RAISE EXCEPTION 'firma_kunde_avslutt: % er ikke registrert', p_tenant
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    -- BARE FRA `prove` OG `aktiv`. Fra `utlopt` er det ingenting å si opp —
    -- prøven er alt over, og firmaet venter på en beslutning som ikke er
    -- kundens. Fra `stengt` er hun alt ute, og et nytt kall ville gitt en
    -- forvirrende feil for noe som allerede er slik hun vil ha det.
    IF v_naa NOT IN ('prove', 'aktiv') THEN
        RAISE EXCEPTION 'firma_kunde_avslutt: % kan ikke sies opp herfra',
                        v_naa
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    PERFORM public.firma_sett_status(p_tenant, 'stengt', p_aktor);
END $$;

REVOKE ALL ON FUNCTION firma_kunde_avslutt(TEXT, TEXT) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION firma_kunde_avslutt(TEXT, TEXT) TO disponit;
    END IF;
END $$;

-- Døra kaller `firma_sett_status`, som er migrator-eid og bare grantet til
-- migrator. En SECURITY DEFINER eid av migrator kaller den som migrator, så
-- grantet trengs ikke — men `firma_kunde_avslutt` må da EIES av migrator, og
-- det gjør den: den opprettes uten `SET LOCAL ROLE`, altså av kjøreren.
COMMENT ON FUNCTION firma_kunde_avslutt(TEXT, TEXT) IS
    'Kunden sier opp sitt eget abonnement. Går ÉN vei — til `stengt`. '
    'Gjenåpning er plattformeierens handling, fordi `stengt → aktiv` ville '
    'latt en kunde gi seg selv et betalt abonnement.';
