-- 0001 foundation: extensions, schemas, partition helper, shared run log
-- Source: Cagr Schema Blueprint v3 (platform additions of 16 Oct 2026), adapted for a from-scratch database.

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS clean;
CREATE SCHEMA IF NOT EXISTS fb;     -- feature-bank registry
CREATE SCHEMA IF NOT EXISTS ind;    -- indicators
CREATE SCHEMA IF NOT EXISTS feat;   -- features
CREATE SCHEMA IF NOT EXISTS sig;    -- signals / decisions
CREATE SCHEMA IF NOT EXISTS lbl;    -- labels: built from later sessions; research only

-- ---------------------------------------------------------------------------------------------
-- ops: helpers and the shared run log
-- ---------------------------------------------------------------------------------------------

-- One partition per calendar year for a RANGE(session_date) parent. Scheduled yearly by the
-- maintenance flow. No DEFAULT partition: an uncovered year fails loudly.
CREATE OR REPLACE FUNCTION ops.create_year_partitions(parent regclass, first_year int, last_year int)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    nsp text;
    rel text;
    y   int;
BEGIN
    SELECT n.nspname, c.relname INTO nsp, rel
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.oid = parent;
    FOR y IN first_year .. last_year LOOP
        EXECUTE format('CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
                       nsp, rel || '_y' || y, nsp, rel, make_date(y, 1, 1), make_date(y + 1, 1, 1));
    END LOOP;
END $$;

-- Shared run log: one row per step of every run, linked to the Prefect flow run when there is one.
CREATE TABLE public.run_log (
    run_id              uuid NOT NULL PRIMARY KEY,
    repo                text NOT NULL,
    runner              text NOT NULL,
    status              text NOT NULL CHECK (status IN ('running','success','failed')),
    started_at          timestamptz NOT NULL,
    ended_at            timestamptz,
    error_type          text,
    error_msg           text,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    prefect_flow_run_id uuid,
    rows_written        bigint,
    max_session         date,                   -- latest session the step produced
    peak_mem_mb         integer CHECK (peak_mem_mb > 0)
);
CREATE INDEX run_log_started_idx  ON public.run_log (started_at DESC);
CREATE INDEX run_log_runner_idx   ON public.run_log (repo, runner);
CREATE INDEX run_log_status_idx   ON public.run_log (status, started_at DESC);
CREATE INDEX run_log_prefect_idx  ON public.run_log (prefect_flow_run_id);
