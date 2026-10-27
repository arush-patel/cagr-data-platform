-- depends: 0004_seed_xnse
-- Let the pipeline ANALYZE the bar table after each bulk load (spec 0001, section 6.3).
-- Autovacuum never analyzes a partitioned parent, and only its owner may, unless granted
-- MAINTAIN (PostgreSQL 17). Granted on the parent and on every partition, since ANALYZE of the
-- parent also analyzes each partition. On an older server (a developer's laptop) this is a no-op
-- and ANALYZE by the writer only warns.

DO $$
DECLARE
    rel regclass;
BEGIN
    IF current_setting('server_version_num')::int < 170000 THEN
        RAISE NOTICE 'MAINTAIN needs PostgreSQL 17; skipped';
        RETURN;
    END IF;
    FOR rel IN
        SELECT 'raw.nse_cm_bar'::regclass
        UNION ALL
        SELECT inhrelid::regclass FROM pg_inherits WHERE inhparent = 'raw.nse_cm_bar'::regclass
    LOOP
        EXECUTE format('GRANT MAINTAIN ON TABLE %s TO cagr_writer', rel);
    END LOOP;
END
$$;
