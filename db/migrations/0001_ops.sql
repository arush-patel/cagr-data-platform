-- Runs and data-quality checks (spec 0001, section 6.1).

CREATE SCHEMA ops;

CREATE TABLE ops.run (
    run_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dagster_run_id  text,
    kind            text NOT NULL CHECK (kind IN ('nightly', 'backfill', 'revision_check', 'manual')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'succeeded', 'failed')),
    code_version    text,
    peak_mem_mb     integer CHECK (peak_mem_mb >= 0),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK ((status = 'running') = (finished_at IS NULL))
);

-- Check definitions are data: thresholds live in params, not in code.
CREATE TABLE ops.dq_check (
    check_id      text PRIMARY KEY CHECK (check_id ~ '^[a-z][a-z0-9_]*$'),
    layer         text NOT NULL CHECK (layer IN ('raw', 'ref', 'clean', 'features', 'serve')),
    feed_id       text,  -- references raw.feed; the foreign key is added in 0003
    severity      text NOT NULL CHECK (severity IN ('critical', 'warn', 'info')),
    description   text NOT NULL,
    params        jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(params) = 'object'),
    owner_action  text NOT NULL,
    active        boolean NOT NULL DEFAULT true
);

CREATE TABLE ops.dq_result (
    result_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          bigint NOT NULL REFERENCES ops.run,
    check_id        text NOT NULL REFERENCES ops.dq_check,
    partition_date  date,
    passed          boolean NOT NULL,
    observed        jsonb NOT NULL DEFAULT '{}'::jsonb,
    detail          text,
    checked_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dq_result_check_date ON ops.dq_result (check_id, partition_date);
CREATE INDEX dq_result_run ON ops.dq_result (run_id);

GRANT USAGE ON SCHEMA ops TO cagr_writer, cagr_read;
GRANT SELECT ON ALL TABLES IN SCHEMA ops TO cagr_read;
REVOKE ALL ON ALL TABLES IN SCHEMA ops FROM cagr_writer;
GRANT SELECT, INSERT, UPDATE ON ops.run TO cagr_writer;
GRANT SELECT ON ops.dq_check TO cagr_writer;
GRANT SELECT, INSERT ON ops.dq_result TO cagr_writer;
