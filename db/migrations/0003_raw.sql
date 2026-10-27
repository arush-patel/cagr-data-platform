-- depends: 0002_ref_calendar
-- What vendors sent, as received (spec 0001, section 6.3). Append-only: rows are never
-- updated or deleted, and every row points at the file it came from.

CREATE SCHEMA raw;

CREATE TABLE raw.vendor (
    vendor_id         text PRIMARY KEY CHECK (vendor_id ~ '^[a-z][a-z0-9_]*$'),
    base_url          text NOT NULL,
    rate_limit_per_s  numeric(5, 2) NOT NULL CHECK (rate_limit_per_s > 0)
);

-- One row per feed and file format. Feeds of the same dataset (the two bhavcopy formats) are
-- ranked by priority: where both exist for a date, the lower number is the one read downstream.
CREATE TABLE raw.feed (
    feed_id         text PRIMARY KEY CHECK (feed_id ~ '^[a-z][a-z0-9_]*$'),
    vendor_id       text NOT NULL REFERENCES raw.vendor,
    dataset         text NOT NULL,
    priority        smallint NOT NULL DEFAULT 1 CHECK (priority >= 1),
    format          text NOT NULL,
    url_template    text NOT NULL,
    available_from  date,
    available_to    date,  -- null: still published
    cadence         text NOT NULL CHECK (cadence IN ('session', 'daily', 'weekly')),
    schedule_cron   text,
    active          boolean NOT NULL DEFAULT true,
    UNIQUE (dataset, priority),
    CHECK (available_to IS NULL OR available_to >= available_from)
);
COMMENT ON COLUMN raw.feed.cadence IS
    'session: one file per calendar day, partitioned; daily/weekly: a dated snapshot';

-- Every HTTP request, including 404s and failures.
CREATE TABLE raw.fetch_attempt (
    attempt_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          bigint REFERENCES ops.run,
    feed_id         text NOT NULL REFERENCES raw.feed,
    partition_date  date NOT NULL,
    url             text NOT NULL,
    http_status     smallint,
    error           text,
    elapsed_ms      integer CHECK (elapsed_ms >= 0),
    attempted_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (http_status IS NOT NULL OR error IS NOT NULL)
);
CREATE INDEX fetch_attempt_feed_date ON raw.fetch_attempt (feed_id, partition_date);

-- A receipt for every file received. It is written in the same transaction as the file's rows.
CREATE TABLE raw.vendor_file (
    file_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_id             text NOT NULL REFERENCES raw.feed,
    partition_date      date NOT NULL,  -- the session date, or the snapshot date
    url                 text NOT NULL,
    sha256              text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    bytes               bigint NOT NULL CHECK (bytes > 0),
    storage_uri         text NOT NULL,
    received_at         timestamptz NOT NULL DEFAULT now(),
    run_id              bigint REFERENCES ops.run,
    revision            integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    supersedes_file_id  bigint REFERENCES raw.vendor_file,
    row_count           integer NOT NULL CHECK (row_count >= 0),
    UNIQUE (feed_id, partition_date, revision),
    UNIQUE (feed_id, partition_date, sha256),
    CHECK ((revision = 1) = (supersedes_file_id IS NULL))
);

-- The file read downstream for each dataset and date: the preferred feed, latest revision.
CREATE VIEW raw.vendor_file_current AS
SELECT DISTINCT ON (fd.dataset, vf.partition_date)
       fd.dataset, vf.*
  FROM raw.vendor_file vf
  JOIN raw.feed fd USING (feed_id)
 ORDER BY fd.dataset, vf.partition_date, fd.priority, vf.revision DESC;

-- Equity bhavcopy, both formats, one row per symbol and series per file.
CREATE TABLE raw.nse_cm_bar (
    file_id           bigint NOT NULL REFERENCES raw.vendor_file,
    trade_date        date NOT NULL,
    symbol            text NOT NULL,
    series            text NOT NULL,  -- '' where the file leaves it blank
    isin              text CHECK (isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),  -- null until 21 Jun 2011
    instrument_type   text,  -- UDiFF FinInstrmTp; null in legacy files
    open              numeric(18, 4),
    high              numeric(18, 4),
    low               numeric(18, 4),
    close             numeric(18, 4),
    last              numeric(18, 4),
    prev_close        numeric(18, 4),
    volume            bigint CHECK (volume >= 0),
    traded_value_inr  numeric(22, 2) CHECK (traded_value_inr >= 0),
    trade_count       bigint CHECK (trade_count >= 0),  -- null until 21 Jun 2011
    source_format     text NOT NULL,
    PRIMARY KEY (trade_date, file_id, symbol, series)
) PARTITION BY RANGE (trade_date);

-- Yearly partitions, created here and by later migrations, never by the pipeline. There is no
-- default partition: a row outside them fails loudly instead of landing somewhere unexpected.
DO $$
BEGIN
    FOR y IN 2010..2030 LOOP
        EXECUTE format(
            'CREATE TABLE raw.nse_cm_bar_%s PARTITION OF raw.nse_cm_bar
                 FOR VALUES FROM (%L) TO (%L)',
            y, make_date(y, 1, 1), make_date(y + 1, 1, 1));
    END LOOP;
END
$$;

CREATE TABLE raw.nse_index_close (
    file_id        bigint NOT NULL REFERENCES raw.vendor_file,
    trade_date     date NOT NULL,
    index_name     text NOT NULL,
    open           numeric(18, 4),
    high           numeric(18, 4),
    low            numeric(18, 4),
    close          numeric(18, 4),
    points_change  numeric(18, 4),
    pct_change     numeric(10, 4),
    volume         bigint CHECK (volume >= 0),
    turnover_inr   numeric(22, 2) CHECK (turnover_inr >= 0),  -- the file says crores
    pe             numeric(12, 4),
    pb             numeric(12, 4),
    div_yield      numeric(10, 4),
    PRIMARY KEY (file_id, index_name)
);
CREATE INDEX nse_index_close_date ON raw.nse_index_close (trade_date);

CREATE TABLE raw.nse_holiday (
    file_id      bigint NOT NULL REFERENCES raw.vendor_file,
    segment      text NOT NULL,
    date         date NOT NULL,
    description  text,
    weekday      text,
    PRIMARY KEY (file_id, segment, date)
);

CREATE TABLE raw.nse_equity_list (
    file_id        bigint NOT NULL REFERENCES raw.vendor_file,
    snapshot_date  date NOT NULL,
    board          text NOT NULL CHECK (board IN ('main', 'sme')),
    symbol         text NOT NULL,
    name           text,
    series         text,
    listing_date   date,
    paid_up_value  numeric(18, 4),
    market_lot     integer,
    isin           text,
    face_value     numeric(18, 4),
    PRIMARY KEY (file_id, symbol)
);

CREATE TABLE raw.nse_symbol_change (
    file_id        bigint NOT NULL REFERENCES raw.vendor_file,
    snapshot_date  date NOT NULL,
    company_name   text,
    old_symbol     text NOT NULL,
    new_symbol     text NOT NULL,
    change_date    date NOT NULL,
    PRIMARY KEY (file_id, old_symbol, new_symbol, change_date)
);

CREATE VIEW raw.nse_cm_bar_current AS
SELECT b.* FROM raw.nse_cm_bar b JOIN raw.vendor_file_current f USING (file_id);

CREATE VIEW raw.nse_index_close_current AS
SELECT c.* FROM raw.nse_index_close c JOIN raw.vendor_file_current f USING (file_id);

ALTER TABLE ops.dq_check
    ADD FOREIGN KEY (feed_id) REFERENCES raw.feed;
ALTER TABLE ref.calendar_exception
    ADD FOREIGN KEY (source_file_id) REFERENCES raw.vendor_file;

-- The writer may only add rows here. Default privileges on cagr_test give it full DML on new
-- tables, so UPDATE, DELETE and TRUNCATE are taken back explicitly; a later migration that adds
-- a partition or table to raw does the same.
GRANT USAGE ON SCHEMA raw TO cagr_writer, cagr_read;
GRANT SELECT ON ALL TABLES IN SCHEMA raw TO cagr_read;
REVOKE ALL ON ALL TABLES IN SCHEMA raw FROM cagr_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA raw TO cagr_writer;
GRANT INSERT ON raw.fetch_attempt, raw.vendor_file, raw.nse_cm_bar, raw.nse_index_close,
                raw.nse_holiday, raw.nse_equity_list, raw.nse_symbol_change TO cagr_writer;
