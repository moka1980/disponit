-- ============================================================
-- 018 — Kanonisk hostname (oppfølging av 016 · PR-014b)
--
-- Codex P1 på #31, landet ETTER at PR-en ble merget: `domenekontroll.hostname`
-- var DOKUMENTERT som «IDNA2008 A-label, lowercase, uten avsluttende punktum»,
-- men INGENTING håndhevet det.
--
-- DNS er case-insensitivt og behandler `example.no` og `example.no.` som samme
-- navn. PostgreSQL gjør ikke det: `example.no`, `EXAMPLE.NO` og `example.no.`
-- ble TRE forskjellige nøkler i `domenekontroll` (PK), i delindeksen
-- `en_verifisert_per_hostname` og i `hostname_binding`. To tenanter kunne
-- derfor stå `verifisert` for det SAMME DNS-navnet samtidig, hver med sin egen
-- binding — og hele B4-overtakelsesadjudikeringen ble aldri utløst, fordi den
-- kun ser konflikter innenfor én tekstlig form. Advisory-låsen i §2
-- (`'domene:' || p_hostname`) er avledet av den samme strengen og sprikte
-- likedan, så de to verifiseringene serialiserte ikke engang mot hverandre.
-- `hostname_binding` er den globale serialiseringsautoriteten i §3 B2; en
-- autoritet som kan ha to navn for én ressurs er ingen autoritet.
--
-- ROTEN er at det fantes MER ENN ÉN gyldig representasjon av ett navn.
-- Vi VALIDERER derfor i stedet for å normalisere: en ikke-kanonisk form
-- AVVISES, den blir ikke stilltiende omskrevet. Normalisering ville lukket
-- nøkkelkollisjonen i basen, men latt to representasjoner leve videre i
-- applikasjonslaget — `opprett_overtakelsessak` nøkler M-37-saken på
-- hostnavnet den får, så `example.no` og `example.no.` ville gitt to saker for
-- én konflikt. Én form inn, én form lagret, én form i idempotensnøkkelen.
--
-- Predikatet håndheves TO steder, og det er med vilje:
--   * LAGRING: CHECK på hver tabell som bærer et hostnavn. Dette er selve
--     sikkerhetsgjerdet — ingen skrivevei kan legge inn en annen form, heller
--     ikke en privilegert INSERT utenom §2-funksjonene.
--   * INNGANG: `krev_kanonisk_hostname` som FØRSTE setning i hver herdede
--     §2-funksjon, FØR advisory-låsen tas og før noe skrives. Gir feilkoden
--     applikasjonen alt oversetter (`invalid_parameter_value` → 400) i stedet
--     for en CheckViolation halvveis inne i transaksjonen, og sikrer at låsen
--     alltid tas på den kanoniske nøkkelen.
--
-- HVORFOR EN NY FIL, IKKE EN ENDRING I 016: migrasjonshistorikken er immutable
-- (`db/kjorer.py` avviser en fil hvis checksum er endret etter kjøring), og 016
-- er alt merget og kjørt. Funksjonene under er derfor GJENGITT i sin helhet med
-- gjerdet lagt til — samme mønster som 004 bruker for `verifiser_token` fra 003.
-- 016-kroppene er superseded; DENNE filen er den gjeldende definisjonen.
-- ============================================================

-- ------------------------------------------------------------
-- §0a Predikatet. IMMUTABLE, så det kan stå i en CHECK.
--
-- U-labels (unicode) avvises: kalleren må punycode-kode til A-label (`xn--`)
-- FØR kallet — ellers kom «samme navn i to former» rett inn igjen via
-- unicode-normalisering. IP-literaler avvises (siste label kan ikke være rent
-- numerisk): de er ikke DNS-soner og kan ikke DNS-verifiseres.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.er_kanonisk_hostname(p_hostname TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog AS $fn$
    -- Hver label: 1–63 tegn fra [a-z0-9-], aldri bindestrek først eller sist.
    -- Minst to labels (FQDN). Totalt ≤ 253 tegn (DNS-navnegrensen).
    SELECT p_hostname IS NOT NULL
       AND length(p_hostname) <= 253
       AND p_hostname ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$'
       AND p_hostname !~ '\.[0-9]+$'
$fn$;

-- ------------------------------------------------------------
-- §0b Inngangsgjerdet: returnerer hostnavnet uendret, eller avviser.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.krev_kanonisk_hostname(p_hostname TEXT)
RETURNS TEXT LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog AS $fn$
BEGIN
    IF NOT public.er_kanonisk_hostname(p_hostname) THEN
        RAISE EXCEPTION 'hostname % er ikke kanonisk (krever IDNA2008 A-label: '
            'små bokstaver, ASCII, minst to labels, uten avsluttende punktum)',
            coalesce(quote_literal(p_hostname), '(null)')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN p_hostname;
END $fn$;

-- ------------------------------------------------------------
-- §0c Lagringsgjerdet. Hver tabell som bærer et hostnavn tar predikatet som
-- CHECK. Idempotent (migrasjonen kan kjøres mot en base der den alt står), og
-- lagt til med ALTER fordi tabellene ble opprettet i 016.
--
-- Constrainten VALIDERES mot eksisterende rader med vilje: en rad i en av disse
-- tre tabellene ER evidens, og en ikke-kanonisk rad betyr at to tenanter kan ha
-- delt et navn. Da skal migrasjonen stoppe høylytt, ikke gjerde inn fremtiden
-- og la det gamle avviket ligge usynlig (`NOT VALID`).
-- ------------------------------------------------------------
DO $do$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['domenekontroll','domenekontroll_hendelse',
                             'hostname_binding'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                        WHERE conname = t || '_hostname_kanonisk'
                          AND conrelid = ('public.' || t)::regclass) THEN
            EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I'
                           ' CHECK (public.er_kanonisk_hostname(hostname))',
                           t, t || '_hostname_kanonisk');
        END IF;
    END LOOP;
END $do$;

-- ============================================================
-- §2 (fra 016) — herdede funksjoner, gjengitt med §0-gjerdet som første
-- setning. Eier og rettigheter er uendret: CREATE OR REPLACE beholder både
-- eierskap og GRANTs, og rollen settes her fordi bare eieren kan erstatte dem.
-- ============================================================
SET LOCAL ROLE disponit_domene_eier;

CREATE OR REPLACE FUNCTION utsted_challenge(
    p_tenant     TEXT,
    p_hostname   TEXT,
    p_wildcard   BOOLEAN,
    p_token_hash TEXT,
    p_aktor      TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    p_hostname := public.krev_kanonisk_hostname(p_hostname);   -- §0-gjerdet
    INSERT INTO public.domenekontroll (tenant, hostname, status, wildcard,
        challenge_token_hash, challenge_utstedt, challenge_utloper)
        VALUES (p_tenant, p_hostname, 'ventende', p_wildcard, p_token_hash,
                now(), now() + interval '7 days')
    ON CONFLICT (tenant, hostname) DO UPDATE
        SET challenge_token_hash = p_token_hash, challenge_utstedt = now(),
            challenge_utloper = now() + interval '7 days',
            -- Codex: en re-utstedelse skal IKKE kunne utvide scope. Mens raden er
            -- verifisert ELLER avventer M-37-avklaring beholdes gammel wildcard —
            -- ellers kunne B reutstede en wildcard-challenge mens den står i
            -- avklaring_kreves, og avgjor_domeneovertakelse godkjenne den utvidede
            -- scopen UTEN at den nye wildcard-challengen noen gang ble verifisert.
            -- En scope-oppgradering krever en ny, verifisert wildcard-challenge.
            wildcard = CASE WHEN public.domenekontroll.status
                                 IN ('verifisert','avklaring_kreves')
                            THEN public.domenekontroll.wildcard
                            ELSE p_wildcard END;   -- status uendret
    INSERT INTO public.domenekontroll_hendelse
        (tenant, hostname, hendelse, aktor) VALUES
        (p_tenant, p_hostname, 'challenge_utstedt', p_aktor);
END $$;

CREATE OR REPLACE FUNCTION verifiser_domenekontroll(
    p_tenant   TEXT,
    p_hostname TEXT,
    p_wildcard BOOLEAN,
    p_aktor    TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eier TEXT; v_status_a TEXT; v_utloper_a TIMESTAMPTZ; v_status_b TEXT;
        v_motpart TEXT;
BEGIN
    p_hostname := public.krev_kanonisk_hostname(p_hostname);   -- §0-gjerdet
    PERFORM pg_advisory_xact_lock(hashtextextended('domene:' || p_hostname, 0));
    -- Codex: `avklaring_kreves` er TERMINAL FOR DENNE VEIEN. Etter at B har
    -- overtatt en aktiv verifisering står B i avklaring med bindingen på seg —
    -- et RETRY av samme verifisering ville da hoppet over overtakelsesgrenen
    -- (eieren er jo B selv) og falt ned i upserten nedenfor, som satte B rett
    -- til `verifisert` og dermed omgikk hele M-37-avgjørelsen. Kun
    -- `avgjor_domeneovertakelse` kan løfte B ut av avklaring.
    SELECT status, konflikt_motpart INTO v_status_b, v_motpart
      FROM public.domenekontroll
     WHERE tenant = p_tenant AND hostname = p_hostname FOR UPDATE;
    IF v_status_b = 'avklaring_kreves' THEN
        INSERT INTO public.domenekontroll_hendelse
            (tenant, hostname, hendelse, fra_status, til_status, grunn, aktor)
            VALUES (p_tenant, p_hostname, 'verifisering_blokkert',
                    'avklaring_kreves', 'avklaring_kreves',
                    'avventer_overtakelsesavgjorelse', p_aktor);
        RETURN 'avklaring_kreves';
    END IF;
    SELECT tenant INTO v_eier FROM public.hostname_binding
     WHERE hostname = p_hostname;
    -- Codex: en kandidat som ble AVVIST av M-37 står `tilbakekalt` MED bindingen
    -- fortsatt på seg. En re-verifisering ser da seg selv som bindingseier, hopper
    -- over ALLE fremmed-eier-grenene under, og ville upsertet seg rett til
    -- `verifisert` — omgått avvisningen uten en ny godkjenning. Tving den tilbake
    -- gjennom avklaring (ny M-37-sak); kun avgjor_domeneovertakelse kan verifisere.
    --
    -- Codex (denne runden): grenen returnerte `avklaring_kreves` — SAMME verdi som
    -- et retry av en alt pågående sak. `opprett_overtakelsessak` lages KUN fra
    -- `konflikt:<tapt-tenant>`, så reapplikasjonen fikk verken konfliktsignalet
    -- eller motparten: kandidaten ble stående `avklaring_kreves` uten noen fersk
    -- sak som kunne nå `avgjor_domeneovertakelse` — permanent limbo. Generasjonen
    -- økes (idempotensnøkkelen er hostname+generasjon → NY sak), og konflikten
    -- returneres med motparten saken skal navngi.
    --
    -- Gjerdet er `konflikt_motpart IS NOT NULL`, ikke `tilbakekalt` alene: kun en
    -- rad som HAR stått i en M-37-konflikt bærer en motpart. En tenant som ble
    -- tilbakekalt av en operatør (aldri i avklaring) har ingen motpart, ingen sak
    -- å gjenåpne og ingen avgjørelse å omgå — den følger den dokumenterte veien
    -- «tilbakekalt eier kan verifisere på nytt» (B4 rad 2) i stedet for å bli
    -- låst inne i en avklaring ingen kan avslutte.
    IF v_eier IS NOT DISTINCT FROM p_tenant AND v_status_b = 'tilbakekalt'
       AND v_motpart IS NOT NULL THEN
        UPDATE public.domenekontroll
           SET status = 'avklaring_kreves', wildcard = p_wildcard,
               autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
         WHERE tenant = p_tenant AND hostname = p_hostname;
        INSERT INTO public.domenekontroll_hendelse
            (tenant, hostname, hendelse, fra_status, til_status, grunn, aktor)
            VALUES (p_tenant, p_hostname, 'avklaring_kreves', 'tilbakekalt',
                    'avklaring_kreves', 'reapplication_etter_avvisning', p_aktor);
        RETURN 'konflikt:' || v_motpart;
    END IF;
    IF v_eier IS NOT NULL AND v_eier IS DISTINCT FROM p_tenant THEN
        SELECT status, utloper INTO v_status_a, v_utloper_a
          FROM public.domenekontroll
         WHERE tenant = v_eier AND hostname = p_hostname;    -- BYPASSRLS
        IF v_status_a = 'verifisert' AND now() < v_utloper_a THEN
            -- B4 rad 1: overtakelse fjerner A, men gir den ikke bort til B.
            UPDATE public.domenekontroll
               SET status = 'tilbakekalt',
                   autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
             WHERE tenant = v_eier AND hostname = p_hostname;
            INSERT INTO public.domenekontroll_hendelse
                (tenant, hostname, hendelse, fra_status, til_status,
                 grunn, aktor) VALUES
                (v_eier, p_hostname, 'overtatt', 'verifisert', 'tilbakekalt',
                 'overtatt_dns_kontroll', p_aktor);
            INSERT INTO public.domenekontroll (tenant, hostname, status, wildcard,
                autorisasjonsgenerasjon, konflikt_motpart)
                VALUES (p_tenant, p_hostname, 'avklaring_kreves', p_wildcard, 1,
                        v_eier)
            ON CONFLICT (tenant, hostname) DO UPDATE
                SET status = 'avklaring_kreves',
                    -- Codex P2: bær den NETTOPP verifiserte wildcard-scopen inn i
                    -- avklaringsraden. Ellers kunne en tenant med en gammel
                    -- wildcard-rad fullføre en eksakt-host-overtakelse og etter
                    -- M-37-godkjenning bli verifisert med den gamle scopen.
                    wildcard = p_wildcard,
                    -- Motparten saken navngir (= `konflikt:<tapt-tenant>` under).
                    konflikt_motpart = v_eier,
                    autorisasjonsgenerasjon = public.domenekontroll.autorisasjonsgenerasjon + 1;
            INSERT INTO public.domenekontroll_hendelse
                (tenant, hostname, hendelse, til_status, grunn, aktor) VALUES
                (p_tenant, p_hostname, 'avklaring_kreves', 'avklaring_kreves',
                 'overtatt_dns_kontroll', p_aktor);
            INSERT INTO public.hostname_binding (hostname, tenant)
                VALUES (p_hostname, p_tenant)
            ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
            RETURN 'konflikt:' || v_eier;
        ELSIF v_status_a = 'avklaring_kreves' THEN
            -- Codex: hostnavnet er under AKTIV M-37-avklaring (bindingseieren
            -- v_eier avventer avgjørelse). En TREDJE tenant som verifiserer er en
            -- ny konfliktpart — den går OGSÅ i avklaring_kreves, ALDRI direkte
            -- verifisert. Uten denne grenen falt et ANNET tenant-forsøk gjennom
            -- til direkte-verifisering, så en DNS-kontrollør kunne omgå M-37 ved å
            -- forsøke overtakelsen to ganger under ulike tenanter. Kun
            -- avgjor_domeneovertakelse løfter noen ut av avklaring.
            INSERT INTO public.domenekontroll (tenant, hostname, status, wildcard,
                autorisasjonsgenerasjon, konflikt_motpart)
                VALUES (p_tenant, p_hostname, 'avklaring_kreves', p_wildcard, 1,
                        v_eier)
            ON CONFLICT (tenant, hostname) DO UPDATE
                SET status = 'avklaring_kreves', wildcard = p_wildcard,
                    konflikt_motpart = v_eier,
                    autorisasjonsgenerasjon = public.domenekontroll.autorisasjonsgenerasjon + 1;
            INSERT INTO public.domenekontroll_hendelse
                (tenant, hostname, hendelse, til_status, grunn, aktor) VALUES
                (p_tenant, p_hostname, 'avklaring_kreves', 'avklaring_kreves',
                 'samtidig_overtakelseskonflikt', p_aktor);
            INSERT INTO public.hostname_binding (hostname, tenant)
                VALUES (p_hostname, p_tenant)
            ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
            RETURN 'konflikt:' || v_eier;
        ELSIF v_status_a = 'verifisert' THEN
            -- Codex: A er verifisert men UTLØPT. Delindeksen en_verifisert_per_
            -- hostname predikerer kun på status, så A-raden blokkerer B med unique
            -- violation. Sett A → utlopt (+gen++) FØR B verifiseres, ellers nås
            -- den dokumenterte direkte-overføringen (B4 rad 2) aldri ved naturlig
            -- utløp.
            UPDATE public.domenekontroll
               SET status = 'utlopt',
                   autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
             WHERE tenant = v_eier AND hostname = p_hostname;
            INSERT INTO public.domenekontroll_hendelse
                (tenant, hostname, hendelse, fra_status, til_status, grunn, aktor)
                VALUES (v_eier, p_hostname, 'utlopt', 'verifisert', 'utlopt',
                        'utlopt_ved_overforing', p_aktor);
        END IF;
        -- A er utlopt/tilbakekalt → B kan verifiseres direkte (B4 rad 2).
    END IF;
    INSERT INTO public.domenekontroll (tenant, hostname, status, wildcard,
        autorisasjonsgenerasjon, verifisert_ts, siste_vellykkede_revalidering,
        utloper)
        VALUES (p_tenant, p_hostname, 'verifisert', p_wildcard, 1, now(), now(),
                now() + interval '90 days')
    ON CONFLICT (tenant, hostname) DO UPDATE
        SET status = 'verifisert', wildcard = p_wildcard,
            autorisasjonsgenerasjon = public.domenekontroll.autorisasjonsgenerasjon + 1,
            -- Autorisasjonen er i havn: konflikten raden bar er over, og
            -- markøren skal ikke sende en senere, ordinær tilbakekalling
            -- inn i en avklaring det ikke finnes noen motpart for.
            konflikt_motpart = NULL,
            verifisert_ts = now(), siste_vellykkede_revalidering = now(),
            utloper = now() + interval '90 days';
    INSERT INTO public.domenekontroll_hendelse
        (tenant, hostname, hendelse, til_status, aktor) VALUES
        (p_tenant, p_hostname, 'verifisert', 'verifisert', p_aktor);
    INSERT INTO public.hostname_binding (hostname, tenant)
        VALUES (p_hostname, p_tenant)
    ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
    RETURN 'verifisert';
END $$;

CREATE OR REPLACE FUNCTION revalider_domenekontroll(
    p_tenant TEXT, p_hostname TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    p_hostname := public.krev_kanonisk_hostname(p_hostname);   -- §0-gjerdet
    UPDATE public.domenekontroll SET siste_vellykkede_revalidering = now()
     WHERE tenant = p_tenant AND hostname = p_hostname AND status = 'verifisert';
    -- Codex: KUN registrer revisjonshendelsen når NØYAKTIG én verifisert rad
    -- faktisk ble oppdatert. Racet en planlagt revalidering med en tilbakekalling/
    -- overtakelse (eller ble kalt for et ukjent/ikke-verifisert hostnavn), traff
    -- UPDATE-en null rader — men append-only-loggen påsto likevel en vellykket
    -- revalidering ETTER at autorisasjonen var trukket, uten noen tilsvarende
    -- endring i siste_vellykkede_revalidering.
    IF NOT FOUND THEN
        RAISE EXCEPTION 'revalider_domenekontroll: %/% er ikke verifisert '
            '(ingen revalidering registrert)', p_tenant, p_hostname
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.domenekontroll_hendelse
        (tenant, hostname, hendelse, aktor) VALUES
        (p_tenant, p_hostname, 'revalidert', p_aktor);
END $$;

CREATE OR REPLACE FUNCTION tilbakekall_domenekontroll(
    p_tenant TEXT, p_hostname TEXT, p_grunn TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    p_hostname := public.krev_kanonisk_hostname(p_hostname);   -- §0-gjerdet
    PERFORM pg_advisory_xact_lock(hashtextextended('domene:' || p_hostname, 0));
    SELECT status INTO v_status FROM public.domenekontroll
     WHERE tenant = p_tenant AND hostname = p_hostname FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'tilbakekall: ukjent domenekontroll %/%', p_tenant, p_hostname
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status = 'tilbakekalt' THEN RETURN; END IF;   -- idempotent
    UPDATE public.domenekontroll
       SET status = 'tilbakekalt',
           autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
     WHERE tenant = p_tenant AND hostname = p_hostname;
    INSERT INTO public.domenekontroll_hendelse
        (tenant, hostname, hendelse, fra_status, til_status, grunn, aktor) VALUES
        (p_tenant, p_hostname, 'tilbakekalt', v_status, 'tilbakekalt', p_grunn, p_aktor);
END $$;

CREATE OR REPLACE FUNCTION avgjor_domeneovertakelse(
    p_tenant TEXT, p_hostname TEXT, p_forventet_generasjon BIGINT,
    p_godkjent BOOLEAN, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_gen BIGINT; v_binding TEXT;
BEGIN
    p_hostname := public.krev_kanonisk_hostname(p_hostname);   -- §0-gjerdet
    PERFORM pg_advisory_xact_lock(hashtextextended('domene:' || p_hostname, 0));
    SELECT status, autorisasjonsgenerasjon INTO v_status, v_gen
      FROM public.domenekontroll
     WHERE tenant = p_tenant AND hostname = p_hostname FOR UPDATE;
    IF v_status IS DISTINCT FROM 'avklaring_kreves' THEN
        RAISE EXCEPTION 'avgjor_domeneovertakelse: %/% er % (krever avklaring_kreves)',
            p_tenant, p_hostname, v_status USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Codex: M-37-saken er nøklet på overtakelsesgenerasjonen, men avgjørelsen
    -- sjekket bare at raden STO i avklaring. Ble en gammel sak liggende mens en
    -- tidligere overtakelse ble avvist og en NY overtakelse satte samme
    -- tenant/hostname tilbake i avklaring, autoriserte den foreldede
    -- godkjenningen den nye generasjonen. Generasjonen leses under RADLÅSEN og
    -- må stemme nøyaktig.
    IF v_gen IS DISTINCT FROM p_forventet_generasjon THEN
        RAISE EXCEPTION 'avgjor_domeneovertakelse: foreldet sak for %/% '
            '(generasjon % <> %)', p_tenant, p_hostname, p_forventet_generasjon,
            v_gen USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Codex P1: generasjonsgjerdet er TENANT-LOKALT, og hostnavnet er globalt.
    -- Tar en tredje tenant C over mens B står i avklaring, flyttes
    -- `hostname_binding` til C — men B-radens status og generasjon står helt
    -- urørt. B sin ELDRE sak kunne derfor godkjennes etterpå, gjøre B verifisert
    -- og skrive bindingen TILBAKE til B, mens C sin nyere konflikt fortsatt lå
    -- uavgjort. Godkjenning gjerdes derfor OGSÅ mot den GJELDENDE
    -- bindingshaveren: kun den som faktisk er dagens utfordrer kan autoriseres.
    -- Bindingen leses under hostname-advisory-låsen (alle skrivere av dette
    -- hostnavnet tar den først), så avlesningen er stabil ut transaksjonen.
    -- AVVISNING står fortsatt åpen — en forbigått utfordrer må kunne ryddes ut
    -- av `avklaring_kreves` uten å få autorisasjon.
    IF p_godkjent THEN
        SELECT tenant INTO v_binding FROM public.hostname_binding
         WHERE hostname = p_hostname;
        IF v_binding IS DISTINCT FROM p_tenant THEN
            RAISE EXCEPTION 'avgjor_domeneovertakelse: %/% er forbigått '
                '(hostname_binding står på %)', p_tenant, p_hostname,
                coalesce(v_binding, '(ingen)')
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        UPDATE public.domenekontroll
           SET status = 'verifisert',
               autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1,
               -- Konflikten er avgjort i utfordrerens favør; markøren nulles.
               -- Ved AVVISNING beholdes den — den er nettopp det som skiller «avvist
               -- av M-37» (må readjudikeres, og motparten er kjent) fra en ordinær
               -- tilbakekalling i verifiseringsveien over.
               konflikt_motpart = NULL,
               verifisert_ts = now(), siste_vellykkede_revalidering = now(),
               utloper = now() + interval '90 days'
         WHERE tenant = p_tenant AND hostname = p_hostname;
        INSERT INTO public.hostname_binding (hostname, tenant)
            VALUES (p_hostname, p_tenant)
        ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
        INSERT INTO public.domenekontroll_hendelse
            (tenant, hostname, hendelse, fra_status, til_status, aktor) VALUES
            (p_tenant, p_hostname, 'avklaring_godkjent', 'avklaring_kreves',
             'verifisert', p_aktor);
    ELSE
        UPDATE public.domenekontroll
           SET status = 'tilbakekalt',
               autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
         WHERE tenant = p_tenant AND hostname = p_hostname;
        INSERT INTO public.domenekontroll_hendelse
            (tenant, hostname, hendelse, fra_status, til_status, aktor) VALUES
            (p_tenant, p_hostname, 'avklaring_avvist', 'avklaring_kreves',
             'tilbakekalt', p_aktor);
    END IF;
END $$;

RESET ROLE;
