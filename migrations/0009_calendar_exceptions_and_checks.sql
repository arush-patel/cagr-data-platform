-- 0009 calendar exceptions and the checks this slice adds.
--
-- The calendar is data (D7). A session list is built from three facts: the weekday rule, the
-- exchange's holiday list, and special sessions the archive proves (Saturday budget days,
-- Muhurat trading). Each fact is a row here with its source, so a rebuild is reproducible and a
-- changed fact can be traced.

CREATE TABLE ref.calendar_exception (
    calendar_id     smallint NOT NULL REFERENCES ref.calendar (calendar_id),
    exception_date  date NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('holiday','special_session')),
    description     text,
    opens_at_local  time,                          -- special sessions only; NULL = the venue's usual hours
    closes_at_local time,
    source          text NOT NULL CHECK (source IN ('nse_holiday_api','archive_evidence','manual')),
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (calendar_id, exception_date),
    CHECK (kind = 'special_session' OR (opens_at_local IS NULL AND closes_at_local IS NULL)),
    CHECK (opens_at_local IS NULL OR closes_at_local > opens_at_local)
);

-- Regular trading hours per calendar, used when a session has no special hours.
ALTER TABLE ref.calendar
    ADD COLUMN regular_open  time,
    ADD COLUMN regular_close time,
    ADD COLUMN weekend_days  smallint[] NOT NULL DEFAULT '{6,7}';   -- ISO day numbers
UPDATE ref.calendar SET regular_open = '09:15', regular_close = '15:30' WHERE code = 'XNSE';

INSERT INTO ops.dq_check (check_code, layer, severity, threshold, description) VALUES
 ('raw_row_count',         'raw',   'critical', 0.10, 'Equity rows in the primary EOD file within this share of the 20-session median'),
 ('prev_close_continuity', 'clean', 'critical', 0.01, 'Vendor previous close differs from our previous raw close with no corporate action on file'),
 ('return_outlier',        'clean', 'warn',     0.25, 'Listings with |log return| beyond the threshold and no corporate action on the latest session')
ON CONFLICT (check_code) DO NOTHING;

-- The clean layer consumes dirty ranges too (a vendor revision or an applied corporate action
-- rebuilds bars from raw); feature families consume them through fb.dirty_consumed.
ALTER TABLE clean.dirty_range ADD COLUMN clean_rebuilt_at timestamptz;
CREATE INDEX dirty_range_clean_pending ON clean.dirty_range (venue_id, from_date) WHERE clean_rebuilt_at IS NULL;
