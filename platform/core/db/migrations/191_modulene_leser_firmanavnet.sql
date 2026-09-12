-- 191: DE FIRE MODULENE LESER FIRMANAVNET FRA REGISTERET.
--
-- MÅLT I PROD: firmaets eget navn står kopiert i fire tabeller — `purreplan`
-- (M-23 purring), `kampanjeavsender` (M-44), `kundeserviceavsender` (M-17) og
-- `tilbudsavsender` (M-26). Alle fire sa «Fjordlys Elektro AS», og de stemte
-- — fordi testfirmaet ble satt opp nøye, ett skjema av gangen.
--
-- For et firma som registrerer seg selv er det fire skjemaer det må FINNE og
-- fylle ut før fire moduler kan sende noe, og ingen av dem nevner de tre
-- andre. Det er nøyaktig samme klasse som `kunde_ref` var fritekst i fire
-- tabeller — forskjellen er at her er det firmaet SELV som er duplisert.
--
-- FORMEN ER EN RESERVEVEI, IKKE EN FLYTTING:
--     coalesce(modulens eget navn, firma.navn)
-- Har modulen en egen verdi, vinner den. Det er med vilje: en purring kan
-- signeres «Fjordlys Elektro AS — Økonomi» mens et tilbud er signert med
-- firmanavnet alene. Registeret fjerner OPPSETTSTEGET, ikke muligheten.
--
-- SKJEMAET ER URØRT. Alle fire dørene brukte allerede LEFT JOIN mot sin
-- avsendertabell, så en manglende rad ga NULL, ikke feil. Endringen er
-- derfor ett kolonneuttrykk og én LEFT JOIN per dør.
--
-- DE TRE LESEDØRENE MÅTTE SNUS. `m17_avsenderprofilen`, `m26_avsenderprofilen`
-- og `m44_avsenderen` gjorde `FROM <modultabell> WHERE tenant = ...` uten
-- LEFT JOIN — null rader når raden manglet, og da hjelper ingen coalesce.
-- Nå driver `firma` raden, og modultabellen er LEFT JOIN-en. Finnes ingen av
-- delene, er svaret tomt som før.
--
-- HVORFOR GRANT PÅ TABELLEN OG IKKE EN DØR TIL: `firma` har FORCE RLS med
-- `tenant_isolasjon`, og alle sju funksjonene har allerede kalt
-- `krev_tenantkontekst` før de leser. Modulrollen ser dermed nøyaktig én rad
-- — sin egen tenants — og det er den samme tenanten den allerede leser
-- kundedata for. En ekstra SECURITY DEFINER-dør ville lagt til et ledd uten
-- å smalne noe.
-- ============================================================

GRANT SELECT ON firma TO disponit_fordring_eier;
GRANT SELECT ON firma TO disponit_kampanje_eier;
GRANT SELECT ON firma TO disponit_kundeservice_eier;
GRANT SELECT ON firma TO disponit_prisbok_eier;

-- ------------------------------------------------------------------
-- Kroppene under er hentet med `pg_get_functiondef` fra den kjørende basen
-- og endret ETT sted hver, i stedet for skrevet av på nytt fra migrasjonene
-- 149/156/163/172. Avskrift er der en stille forskjell oppstår.
--
-- `CREATE OR REPLACE` beholder eier og ACL, så ingen grant gjentas her —
-- og ingen ny `EXECUTE TO PUBLIC` oppstår. Porten måler ACL-ene etterpå.
-- ------------------------------------------------------------------

-- m17_avsenderprofilen (eier disponit_kundeservice_eier)
SET LOCAL ROLE disponit_kundeservice_eier;
CREATE OR REPLACE FUNCTION public.m17_avsenderprofilen(p_tenant text)
 RETURNS TABLE(avsender_navn text, svar_til text, signatur text, oppdatert timestamp with time zone)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_avsenderprofilen');
    RETURN QUERY
    SELECT coalesce(a.avsender_navn, fx.navn), a.svar_til, a.signatur, a.oppdatert
      FROM (SELECT p_tenant AS tenant) src
      LEFT JOIN public.firma fx ON fx.tenant = src.tenant
      LEFT JOIN public.kundeserviceavsender a ON a.tenant = src.tenant
     WHERE fx.tenant IS NOT NULL OR a.tenant IS NOT NULL;
END $function$;
RESET ROLE;

-- m26_avsenderprofilen (eier disponit_prisbok_eier)
SET LOCAL ROLE disponit_prisbok_eier;
CREATE OR REPLACE FUNCTION public.m26_avsenderprofilen(p_tenant text)
 RETURNS TABLE(avsender_navn text, svar_til text, signatur text, oppdatert timestamp with time zone)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_avsenderprofilen');
    RETURN QUERY
    SELECT coalesce(a.avsender_navn, fx.navn), a.svar_til, a.signatur, a.oppdatert
      FROM (SELECT p_tenant AS tenant) src
      LEFT JOIN public.firma fx ON fx.tenant = src.tenant
      LEFT JOIN public.tilbudsavsender a ON a.tenant = src.tenant
     WHERE fx.tenant IS NOT NULL OR a.tenant IS NOT NULL;
END $function$;
RESET ROLE;

-- m44_avsenderen (eier disponit_kampanje_eier)
SET LOCAL ROLE disponit_kampanje_eier;
CREATE OR REPLACE FUNCTION public.m44_avsenderen(p_tenant text)
 RETURNS TABLE(avsender_navn text, svar_til text, oppdatert timestamp with time zone)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_avsenderen');
    RETURN QUERY
    SELECT coalesce(a.avsender_navn, fx.navn), a.svar_til, a.oppdatert
      FROM (SELECT p_tenant AS tenant) src
      LEFT JOIN public.firma fx ON fx.tenant = src.tenant
      LEFT JOIN public.kampanjeavsender a ON a.tenant = src.tenant
     WHERE fx.tenant IS NOT NULL OR a.tenant IS NOT NULL;
END $function$;
RESET ROLE;

-- m17_for_sending (eier disponit_kundeservice_eier)
SET LOCAL ROLE disponit_kundeservice_eier;
CREATE OR REPLACE FUNCTION public.m17_for_sending(p_tenant text, p_henvendelse_id uuid, p_utkast_id uuid)
 RETURNS TABLE(lukket boolean, i_unntakskoe boolean, kanal text, ekstern_ref text, avsender_maske text, avsender_kryptert bytea, nonce_avsender bytea, avsender_key_id text, emne_kryptert bytea, nonce_emne bytea, key_id text, utkast_henvendelse_id uuid, utkast_status text, utkast_kryptert bytea, utkast_nonce bytea, utkast_key_id text, profil_navn text, profil_svar_til text, profil_signatur text)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_for_sending');
    RETURN QUERY
    SELECT (h.lukket_ts IS NOT NULL), (h.unntak_id IS NOT NULL), h.kanal,
           h.ekstern_ref,
           h.avsender_maske, h.avsender_kryptert, h.nonce_avsender,
           h.avsender_key_id, h.emne_kryptert, h.nonce_emne, h.key_id,
           u.henvendelse_id, u.status, u.tekst_kryptert, u.nonce, u.key_id,
           coalesce(a.avsender_navn, fx.navn), a.svar_til, a.signatur
      FROM public.henvendelse h
      LEFT JOIN public.svarutkast u
        ON u.tenant = h.tenant AND u.utkast_id = p_utkast_id
      LEFT JOIN public.kundeserviceavsender a ON a.tenant = h.tenant
      LEFT JOIN public.firma fx ON fx.tenant = h.tenant
     WHERE h.tenant = p_tenant AND h.henvendelse_id = p_henvendelse_id;
END $function$;
RESET ROLE;

-- m23_for_sending (eier disponit_fordring_eier)
SET LOCAL ROLE disponit_fordring_eier;
CREATE OR REPLACE FUNCTION public.m23_for_sending(p_tenant text, p_fordring_id uuid)
 RETURNS TABLE(status text, kunde_ref text, fakturanummer text, rest_ore bigint, forfall date, trinn integer, neste_trinn integer, handling_trinn text, gebyr_ore bigint, mottaker_maske text, mottaker_kryptert bytea, mottaker_nonce bytea, mottaker_key_id text, avsender_navn text, svar_til text)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_for_sending');
    RETURN QUERY
    SELECT f.status, f.kunde_ref, f.fakturanummer,
           f.belop_ore - f.betalt_ore, f.forfall, f.trinn,
           t.trinn_nr, t.handling, t.gebyr_ore,
           f.mottaker_maske, f.mottaker_kryptert, f.mottaker_nonce,
           f.mottaker_key_id, coalesce(p.avsender_navn, fx.navn), p.svar_til
      FROM public.fordring f
      LEFT JOIN public.purretrinn t
        ON t.tenant = f.tenant AND t.trinn_nr = f.trinn + 1
      LEFT JOIN public.purreplan p ON p.tenant = f.tenant
      LEFT JOIN public.firma fx ON fx.tenant = f.tenant
     WHERE f.tenant = p_tenant AND f.fordring_id = p_fordring_id;
END $function$;
RESET ROLE;

-- m26_for_sending (eier disponit_prisbok_eier)
SET LOCAL ROLE disponit_prisbok_eier;
CREATE OR REPLACE FUNCTION public.m26_for_sending(p_tenant text, p_tilbud_id uuid)
 RETURNS TABLE(status text, gyldig_til date, kunde_navn text, kunde_maske text, kunde_kryptert bytea, nonce_kunde bytea, kunde_key_id text, sum_ore bigint, valuta text, tilbudsdato date, innledning text, linjer jsonb, klausuler jsonb, profil_navn text, profil_svar_til text, profil_signatur text)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_for_sending');
    RETURN QUERY
    SELECT t.status, t.gyldig_til, t.kunde_navn, t.kunde_maske,
           t.kunde_kryptert, t.nonce_kunde, t.kunde_key_id, t.sum_ore,
           t.valuta, t.tilbudsdato, t.innledning, d.linjer, d.klausuler,
           coalesce(a.avsender_navn, fx.navn), a.svar_til, a.signatur
      FROM public.tilbud t
      LEFT JOIN LATERAL public.m26_tilbudet(p_tenant, t.tilbud_id) d ON true
      LEFT JOIN public.tilbudsavsender a ON a.tenant = t.tenant
      LEFT JOIN public.firma fx ON fx.tenant = t.tenant
     WHERE t.tenant = p_tenant AND t.tilbud_id = p_tilbud_id;
END $function$;
RESET ROLE;

-- m44_for_sending (eier disponit_kampanje_eier)
SET LOCAL ROLE disponit_kampanje_eier;
CREATE OR REPLACE FUNCTION public.m44_for_sending(p_tenant text, p_kampanje_id uuid, p_mottaker_id uuid)
 RETURNS TABLE(kampanje_status text, planlagt_sendt date, emne text, tekst text, avmeldingslenke text, i_plan boolean, mottaker_aktiv boolean, mottaker_navn text, kontakt_maske text, kontakt_kryptert bytea, kontakt_nonce bytea, kontakt_key_id text, samtykke_tilstand text, avsender_navn text, svar_til text)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_for_sending');
    RETURN QUERY
    SELECT k.status, k.planlagt_sendt, k.emne, k.tekst, k.avmeldingslenke,
           EXISTS (SELECT 1 FROM public.kampanjeplan pl
                    WHERE pl.tenant = p_tenant
                      AND pl.kampanje_id = p_kampanje_id
                      AND pl.mottaker_id = p_mottaker_id),
           m.aktiv, m.navn, m.kontakt_maske, m.kontakt_kryptert,
           m.kontakt_nonce,
           m.kontakt_key_id, s.tilstand, coalesce(a.avsender_navn, fx.navn), a.svar_til
      FROM public.kampanje k
      LEFT JOIN public.kampanjemottaker m
        ON m.tenant = k.tenant AND m.mottaker_id = p_mottaker_id
      LEFT JOIN LATERAL (
            SELECT s2.tilstand
              FROM public.samtykkehendelse s2
             WHERE s2.tenant = p_tenant AND s2.mottaker_id = p_mottaker_id
               AND s2.inntruffet <= current_date
             ORDER BY s2.inntruffet DESC, s2.registrert DESC
             LIMIT 1) s ON true
      LEFT JOIN public.kampanjeavsender a ON a.tenant = k.tenant
      LEFT JOIN public.firma fx ON fx.tenant = k.tenant
     WHERE k.tenant = p_tenant AND k.kampanje_id = p_kampanje_id;
END $function$;
RESET ROLE;
