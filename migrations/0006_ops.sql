-- 0006 ops: data quality and freshness
-- Source: Cagr Schema Blueprint v3 (platform additions of 16 Oct 2026), adapted for a from-scratch database.

-- ---------------------------------------------------------------------------------------------
-- ops: data quality, freshness
-- ---------------------------------------------------------------------------------------------

CREATE TABLE ops.dq_check (
    check_code      text PRIMARY KEY,
    layer           text NOT NULL CHECK (layer IN ('ref','raw','clean','fb','serve')),
    severity        text NOT NULL CHECK (severity IN ('warn','critical')),  -- critical fails the Prefect flow
    threshold       double precision,
    description     text NOT NULL
);

CREATE TABLE ops.dq_result (
    result_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          uuid NOT NULL REFERENCES public.run_log (run_id),
    check_code      text NOT NULL REFERENCES ops.dq_check (check_code),
    venue_id        smallint REFERENCES ref.venue (venue_id),
    session_date    date,
    listing_id      integer,                       -- NULL = whole run
    observed        double precision,
    passed          boolean NOT NULL,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
    checked_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dq_result_run_idx    ON ops.dq_result (run_id);
CREATE INDEX dq_result_failed_idx ON ops.dq_result (check_code, checked_at DESC) WHERE NOT passed;

CREATE TABLE ops.feed_watermark (
    feed_id         smallint PRIMARY KEY REFERENCES ref.feed (feed_id),
    last_session    date,                          -- latest session successfully ingested
    last_success_at timestamptz,
    last_run_id     uuid REFERENCES public.run_log (run_id)
);

-- Freshness: one row per active feed. Read by the monitoring flow and the dashboard.
CREATE VIEW ops.feed_freshness AS
SELECT f.feed_id, f.code, v.code AS venue, f.dataset, f.priority,
       w.last_session,
       (SELECT max(cs.session_date) FROM ref.calendar_session cs
         WHERE cs.calendar_id = v.calendar_id AND cs.closes_at <= now()) AS expected_session,
       w.last_success_at,
       f.max_staleness,
       (w.last_success_at IS NULL OR now() - w.last_success_at > f.max_staleness) AS is_stale
FROM ref.feed f
JOIN ref.venue v ON v.venue_id = f.venue_id
LEFT JOIN ops.feed_watermark w ON w.feed_id = f.feed_id
WHERE f.active;
