-- depends: 0009_corporate_actions
-- Clean daily, weekly and monthly bars keyed on listing_id, the adjustment steps, and the
-- adjusted and unadjusted views (spec 0003). clean is derived: the builder deletes and inserts
-- a day's rows in one transaction, so a rerun leaves the same rows.

CREATE SCHEMA clean;

CREATE TABLE clean.bar_1d (
    listing_id      integer NOT NULL REFERENCES ref.listing,
    date            date NOT NULL,
    open            numeric(18, 4),
    high            numeric(18, 4),
    low             numeric(18, 4),
    close           numeric(18, 4) NOT NULL,
    volume          bigint CHECK (volume >= 0),
    value_traded    numeric(22, 2) CHECK (value_traded >= 0),  -- rupees, also for indices
    trade_count     bigint CHECK (trade_count >= 0),
    prev_close      numeric(18, 4),  -- as NSE printed it, never adjusted
    series          text NOT NULL,  -- '' for an index
    source_file_id  bigint NOT NULL REFERENCES raw.vendor_file,
    built_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, date)
) PARTITION BY RANGE (date);

DO $$
BEGIN
    FOR y IN 2010..2030 LOOP
        EXECUTE format(
            'CREATE TABLE clean.bar_1d_%s PARTITION OF clean.bar_1d
                 FOR VALUES FROM (%L) TO (%L)',
            y, make_date(y, 1, 1), make_date(y + 1, 1, 1));
    END LOOP;
END
$$;
CREATE INDEX bar_1d_date ON clean.bar_1d (date);

-- The product of every applied factor with an ex-date after the bar's date, per period between
-- ex-dates. Periods with a factor of 1 (after a listing's last ex-date) are left out.
CREATE TABLE clean.adjustment_step (
    listing_id  integer NOT NULL REFERENCES ref.listing,
    valid_from  date NOT NULL,  -- '-infinity' for the first period
    valid_to    date NOT NULL CHECK (valid_to > valid_from),
    cum_factor  double precision NOT NULL CHECK (cum_factor > 0),
    PRIMARY KEY (listing_id, valid_from),
    EXCLUDE USING gist (int4range(listing_id, listing_id, '[]') WITH &&,
                        daterange(valid_from, valid_to) WITH &&)
);

-- Weekly and monthly bars, unadjusted and adjusted (spec 0003, section 5.1).
CREATE TABLE clean.bar_1w (
    listing_id        integer NOT NULL REFERENCES ref.listing,
    period_start      date NOT NULL,
    period_end        date NOT NULL CHECK (period_end >= period_start),
    open              numeric(18, 4),
    high              numeric(18, 4),
    low               numeric(18, 4),
    close             numeric(18, 4) NOT NULL,
    volume            bigint,
    value_traded      numeric(22, 2),
    trade_count       bigint,
    session_count     smallint NOT NULL CHECK (session_count >= 1),
    adj_open          double precision,
    adj_high          double precision,
    adj_low           double precision,
    adj_close         double precision,
    adj_volume        double precision,
    complete          boolean NOT NULL,
    adjustment_state  bigint,  -- the highest applied adjustment_id when built
    built_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, period_start)
);
CREATE TABLE clean.bar_1mo (LIKE clean.bar_1w INCLUDING ALL);
ALTER TABLE clean.bar_1mo ADD FOREIGN KEY (listing_id) REFERENCES ref.listing;

CREATE VIEW clean.bar_1d_unadjusted AS
SELECT listing_id, date, open, high, low, close, volume, value_traded, trade_count, series
  FROM clean.bar_1d;

-- The earliest pending adjustment of each listing holds back its adjusted bars from that
-- ex-date on (spec 0003, section 4).
CREATE VIEW clean.adjustment_hold AS
SELECT DISTINCT ON (listing_id) listing_id, ex_date AS held_from, adjustment_id
  FROM ref.price_adjustment
 WHERE status = 'pending'
 ORDER BY listing_id, ex_date, adjustment_id;

CREATE VIEW clean.bar_1d_adjusted AS
SELECT b.listing_id, b.date,
       b.open::double precision * f.adj_factor AS open,
       b.high::double precision * f.adj_factor AS high,
       b.low::double precision * f.adj_factor AS low,
       b.close::double precision * f.adj_factor AS close,
       b.volume::double precision / f.adj_factor AS volume,
       b.value_traded, b.trade_count, b.series, f.adj_factor
  FROM clean.bar_1d b
  LEFT JOIN clean.adjustment_hold h ON h.listing_id = b.listing_id
  CROSS JOIN LATERAL (
      SELECT coalesce(
          (SELECT s.cum_factor FROM clean.adjustment_step s
            WHERE s.listing_id = b.listing_id AND s.valid_from <= b.date AND s.valid_to > b.date),
          1.0::double precision) AS adj_factor
  ) f
 WHERE h.held_from IS NULL OR b.date < h.held_from;

INSERT INTO ops.dq_check (check_id, layer, feed_id, severity, description, params, owner_action) VALUES
    ('bar_on_session', 'clean', NULL, 'critical',
     'Every bar''s date is an XNSE session', '{}',
     'Block; the calendar or the builder is wrong'),
    ('bars_complete', 'clean', NULL, 'critical',
     'Every raw row in a listing series produced exactly one bar', '{}',
     'Block; the listing build missed a row'),
    ('bar_ohlc_order', 'clean', NULL, 'warn',
     'low <= open, close <= high on each bar', '{}',
     'List the bars; usually a vendor error'),
    ('adjusted_jump', 'clean', NULL, 'warn',
     'No adjusted close moves more than 25% in a session without an adjustment event',
     '{"max_move": 0.25}',
     'Look for a missed corporate action'),
    ('index_benchmark_present', 'clean', NULL, 'critical',
     'Nifty 50 has a bar on every session since 21 Feb 2012',
     '{"index": "Nifty 50", "from": "2012-02-21"}',
     'Block; inspect the index files'),
    ('period_bars_match', 'clean', NULL, 'critical',
     'Each weekly and monthly bar agrees with its daily bars', '{}',
     'Block; rebuild the periods of the listing');

GRANT USAGE ON SCHEMA clean TO cagr_writer, cagr_read;
REVOKE ALL ON ALL TABLES IN SCHEMA clean FROM cagr_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA clean TO cagr_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA clean TO cagr_read;

-- As for raw bars: the writer analyzes the parent after a bulk load (see 0005).
DO $$
DECLARE
    rel regclass;
BEGIN
    IF current_setting('server_version_num')::int < 170000 THEN
        RAISE NOTICE 'MAINTAIN needs PostgreSQL 17; skipped';
        RETURN;
    END IF;
    FOR rel IN
        SELECT 'clean.bar_1d'::regclass
        UNION ALL
        SELECT inhrelid::regclass FROM pg_inherits WHERE inhparent = 'clean.bar_1d'::regclass
    LOOP
        EXECUTE format('GRANT MAINTAIN ON TABLE %s TO cagr_writer', rel);
    END LOOP;
END
$$;
